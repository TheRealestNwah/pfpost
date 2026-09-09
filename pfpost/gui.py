"""Desktop interface (PySide6).

Every network call runs on a worker thread; the UI thread only renders. All
logic lives in api/session/queue - this module is presentation only.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import (QDateTime, QEvent, QSize, Qt, QThread, QTimer,
                            QUrl, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QIcon,
                           QPalette, QPixmap)
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from . import api, queue as pfqueue, scheduler, store, theme
from .session import AuthError, DEFAULT_PORT, Session

THUMB = 56
# How often an open window checks its own queue. The scheduled task
# handles the app being closed; this stops you waiting on its interval.
AUTO_RUN_MS = 60_000
IMAGE_FILTER = ("Media (*.png *.jpg *.jpeg *.gif *.webp *.avif *.heic *.mp4 *.mov);;"
                "All files (*)")


class Worker(QThread):
    """Run one callable off the UI thread."""
    done = Signal(object)
    failed = Signal(str)
    note = Signal(str)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs

    def run(self):
        try:
            self.done.emit(self._fn(*self._args, **self._kwargs))
        except Exception as exc:  # surfaced in the UI, never swallowed
            self.failed.emit(str(exc))


class ConnectDialog(QDialog):
    """Register an OAuth client and complete the browser authorization."""

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.worker = None
        self.setWindowTitle("Connect to Pixelfed")
        self.setMinimumWidth(520)

        self.instance = QLineEdit(session.instance or "")
        self.instance.setPlaceholderText("pixelfed.social")
        self.port = QLineEdit(str(session.state.get("redirect_port", DEFAULT_PORT)))

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMinimumHeight(160)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok = self.buttons.button(QDialogButtonBox.Ok)
        ok.setText("Connect")
        ok.setProperty("accent", True)
        self.buttons.accepted.connect(self.start)
        self.buttons.rejected.connect(self.reject)

        form = QFormLayout()
        form.addRow("Instance domain", self.instance)
        form.addRow("Redirect port", self.port)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(QLabel(
            "pfpost registers its own OAuth client, then opens your browser to "
            "authorize.\nCredentials go into %s." % store.backend_description()))
        layout.addWidget(self.progress)
        layout.addWidget(self.log)
        layout.addWidget(self.buttons)

    def append(self, text: str):
        self.log.appendPlainText(text)

    def start(self):
        domain = api.normalise_instance(self.instance.text())
        if not domain:
            QMessageBox.warning(self, "pfpost", "Enter an instance domain.")
            return
        try:
            port = int(self.port.text())
        except ValueError:
            QMessageBox.warning(self, "pfpost", "Port must be a number.")
            return

        self.buttons.setEnabled(False)
        self.progress.show()
        self.append("Registering on %s ..." % domain)

        def work():
            notes = []
            self.session.register(domain, port=port, on_note=notes.append)
            self.session.authorize(on_note=notes.append)
            return notes

        self.worker = Worker(work)
        self.worker.done.connect(self.finished_ok)
        self.worker.failed.connect(self.finished_bad)
        self.worker.start()

    def finished_ok(self, notes):
        for line in notes:
            self.append(line)
        self.progress.hide()
        self.append("\nConnected.")
        self.accept()

    def finished_bad(self, message):
        self.progress.hide()
        self.buttons.setEnabled(True)
        self.append("\nFailed: %s" % message)


class PostedDialog(QDialog):
    """Success dialog with the post link.

    A QDialog rather than a QMessageBox: message boxes close on any button
    press, so "Copy link" would dismiss the dialog before you could also open
    it. Here only Close closes.
    """

    def __init__(self, url: str | None, parent=None):
        super().__init__(parent)
        # The instance supplies this string, so only ever hand a real web URL
        # to the browser - never an arbitrary URI scheme.
        self.url = url if (url or "").startswith(("http://", "https://")) else None
        self.setWindowTitle("Posted")
        self.setMinimumWidth(460)

        heading = QLabel("Posted successfully.")
        heading.setStyleSheet("font-weight:bold")

        self.link = QLineEdit(self.url or (url or "No link returned by the instance."))
        self.link.setReadOnly(True)
        self.link.setCursorPosition(0)

        self.copy_button = QPushButton("Copy link")
        self.copy_button.clicked.connect(self.copy_link)
        self.open_button = QPushButton("Open in browser")
        self.open_button.clicked.connect(self.open_in_browser)
        self.copy_button.setProperty("accent", True)
        self.close_button = QPushButton("Close")
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)

        for button in (self.copy_button, self.open_button):
            button.setEnabled(self.url is not None)
            if self.url is None:
                button.setToolTip("The instance did not return a link for this post")

        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.link)
        layout.addLayout(buttons)

    def copy_link(self):
        if not self.url:
            return
        QApplication.clipboard().setText(self.url)
        self.link.selectAll()
        self.copy_button.setText("Copied")
        QTimer.singleShot(1500, lambda: self.copy_button.setText("Copy link"))

    def open_in_browser(self):
        if not self.url:
            return
        QDesktopServices.openUrl(QUrl(self.url))


class AccountBar(QWidget):
    """Always-visible connection state. A status-bar message is not an indicator."""

    connect_requested = Signal()
    disconnect_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("accountBar")
        self.dot = QLabel("●")
        self.dot.setFixedWidth(14)
        self.primary = QLabel("Not connected")
        self.primary.setStyleSheet("font-weight:bold")
        self.detail = QLabel("")

        self.button = QPushButton("Connect account")
        self.button.clicked.connect(self._clicked)
        self._connected = False
        self._applying = False
        self._applied_colour = None
        self._last_state: dict = {"configured": False, "connected": False}
        self._last_username = None
        self.tokens = theme.tokens(dark=False)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self.dot)
        layout.addWidget(self.primary)
        layout.addWidget(self.detail, 1)
        layout.addWidget(self.button)
        self.apply_theme()

    def apply_theme(self):
        """Restyle for the current palette.

        setStyleSheet() itself emits a PaletteChange, which re-enters
        changeEvent - so this guards against recursion and does nothing when
        the derived colour has not actually changed.
        """
        if self._applying:
            return
        colour = self.tokens["muted"]
        if colour == self._applied_colour:
            return
        self._applying = True
        try:
            self._applied_colour = colour
            self.detail.setStyleSheet("color:%s" % colour)
        finally:
            self._applying = False

    def set_tokens(self, tokens: dict):
        self.tokens = tokens
        self._applied_colour = None
        self.apply_theme()
        self.show_state(self._last_state, self._last_username)

    def changeEvent(self, event):
        if event.type() == QEvent.PaletteChange:
            self.apply_theme()
        super().changeEvent(event)

    def _clicked(self):
        if self._connected:
            self.disconnect_requested.emit()
        else:
            self.connect_requested.emit()

    def show_state(self, info: dict, username: str | None = None):
        self._last_state, self._last_username = info, username
        self._connected = bool(info.get("connected"))
        if not info.get("configured"):
            self.dot.setStyleSheet("color:%s" % self.tokens["danger"])
            self.primary.setText("Not connected")
            self.detail.setText("Connect an account to start posting")
            self.button.setText("Connect account")
            return
        if not self._connected:
            self.dot.setStyleSheet("color:%s" % self.tokens["warning"])
            self.primary.setText("Not signed in")
            self.detail.setText("Registered on %s - authorization needed"
                                % info.get("instance", "?"))
            self.button.setText("Sign in")
            return

        self.dot.setStyleSheet("color:%s" % self.tokens["success"])
        who = ("@%s" % username) if username else info.get("instance", "?")
        self.primary.setText("Connected — %s" % who)

        bits = []
        if username:
            bits.append(info.get("instance", ""))
        bits.append("scopes: %s" % info.get("scopes"))
        if not info.get("can_read"):
            bits.append("write-only, so the account name is unavailable")
        bits.append("secrets: %s" % info.get("backend"))
        self.detail.setText("  |  ".join(b for b in bits if b))
        self.button.setText("Disconnect")


class Composer(QWidget):
    """Image well, caption, alt text, and the post/schedule actions."""

    posted = Signal(str)
    queued = Signal(str)
    status = Signal(str)

    def __init__(self, session: Session, parent=None, tokens: dict | None = None):
        super().__init__(parent)
        self.tokens = tokens or theme.tokens(dark=False)
        self.session = session
        self.limits = {}
        self.worker = None
        self.setAcceptDrops(True)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["", "File", "Alt text (click to edit)"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(THUMB + 8)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(0, QHeaderView.Fixed)
        head.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        head.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, THUMB + 8)

        add = QPushButton("Add images...")
        add.clicked.connect(self.browse)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_selected)

        self.caption = QPlainTextEdit()
        self.caption.setPlaceholderText("Caption")
        self.caption.setMaximumHeight(90)
        self.caption.textChanged.connect(self.update_counter)
        self.counter = QLabel("0 characters")

        self.visibility = QComboBox()
        self.visibility.addItems(["public", "unlisted", "private"])
        self.visibility.setMinimumWidth(130)

        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.when.setMinimumWidth(230)

        self.post_now = QPushButton("Post now")
        self.post_now.setProperty("accent", True)
        self.post_now.clicked.connect(self.do_post)
        self.schedule = QPushButton("Add to queue")
        self.schedule.clicked.connect(self.do_queue)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        buttons = QHBoxLayout()
        buttons.addWidget(add)
        buttons.addWidget(remove)
        buttons.addStretch()

        meta = QHBoxLayout()
        meta.addWidget(QLabel("Visibility"))
        meta.addWidget(self.visibility)
        meta.addStretch()
        meta.addWidget(self.counter)

        actions = QHBoxLayout()
        actions.addWidget(self.post_now)
        actions.addStretch()
        actions.addWidget(QLabel("Schedule for"))
        actions.addWidget(self.when)
        actions.addWidget(self.schedule)

        layout = QVBoxLayout(self)
        drop_hint = QLabel("Drag images here, or use Add images")
        drop_hint.setObjectName("dropHint")
        layout.addWidget(drop_hint)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        layout.addWidget(self.caption)
        layout.addLayout(meta)
        layout.addWidget(self.progress)
        layout.addLayout(actions)

    # -- drag and drop ----------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        self.add_paths([p for p in paths if p.is_file()])
        event.acceptProposedAction()

    # -- image list -------------------------------------------------------

    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add images", "", IMAGE_FILTER)
        self.add_paths([Path(f) for f in files])

    def add_paths(self, paths):
        for path in paths:
            row = self.table.rowCount()
            self.table.insertRow(row)

            thumb = QTableWidgetItem()
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                thumb.setIcon(QIcon(pixmap.scaled(
                    THUMB, THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
            thumb.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, thumb)

            name = QTableWidgetItem(path.name)
            name.setData(Qt.UserRole, str(path))
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            name.setToolTip(str(path))
            self.table.setItem(row, 1, name)

            self.table.setItem(row, 2, QTableWidgetItem(""))
        self.table.setIconSize(QSize(THUMB, THUMB))
        self.update_counter()

    def remove_selected(self):
        for index in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(index)

    def images(self):
        out = []
        for row in range(self.table.rowCount()):
            path = Path(self.table.item(row, 1).data(Qt.UserRole))
            alt_item = self.table.item(row, 2)
            alt = alt_item.text().strip() if alt_item else ""
            out.append((path, alt or None))
        return out

    def clear(self):
        self.table.setRowCount(0)
        self.caption.setPlainText("")
        self.update_counter()
        store.clear_draft()      # the work left the composer; nothing to restore

    # -- state ------------------------------------------------------------

    def set_limits(self, limits: dict):
        self.limits = limits or {}
        self.update_counter()

    def update_counter(self):
        used = len(self.caption.toPlainText())
        cap = self.limits.get("max_characters")
        self.counter.setText("%d characters" % used if not cap
                             else "%d / %d characters" % (used, cap))
        over = bool(cap and used > cap)
        self.counter.setStyleSheet(
            "color:%s;font-weight:bold" % self.tokens["danger"] if over else "")

    def busy(self, on: bool):
        self.progress.setVisible(on)
        self.post_now.setEnabled(not on)
        self.schedule.setEnabled(not on)

    def set_enabled(self, connected: bool):
        """Posting needs an account; queueing does not, but both are gated so
        the window never looks usable while disconnected."""
        self.post_now.setEnabled(connected)
        self.schedule.setEnabled(connected)
        self.post_now.setToolTip("" if connected else "Connect an account first")

    def gather(self):
        images = self.images()
        if not images:
            QMessageBox.warning(self, "pfpost",
                                "Add at least one image - Pixelfed requires media.")
            return None
        try:
            api.validate(images, self.caption.toPlainText(), self.limits)
        except api.ValidationError as exc:
            QMessageBox.warning(self, "pfpost", str(exc))
            return None
        if not self.confirm_alt_text(images):
            return None
        return images

    def confirm_alt_text(self, images) -> bool:
        """Alt text cannot be added after posting, so ask before, not never."""
        missing = [path.name for path, alt in images if not (alt or "").strip()]
        if not missing:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("No alt text")
        box.setIcon(QMessageBox.Warning)
        box.setText("%d image%s ha%s no alt text."
                    % (len(missing), "" if len(missing) == 1 else "s",
                       "s" if len(missing) == 1 else "ve"))
        box.setInformativeText(
            "Alt text describes the image for people using screen readers, and "
            "Pixelfed cannot add it after posting.\n\n%s"
            % ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))
        add = box.addButton("Add alt text", QMessageBox.RejectRole)
        box.addButton("Post without it", QMessageBox.AcceptRole)
        box.setDefaultButton(add)
        box.exec()
        if box.clickedButton() is add:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 2)
                if item and not item.text().strip():
                    self.table.setCurrentItem(item)
                    self.table.editItem(item)
                    break
            return False
        return True

    # -- drafts -----------------------------------------------------------

    def draft(self) -> dict:
        return {
            "caption": self.caption.toPlainText(),
            "visibility": self.visibility.currentText(),
            "images": [{"path": str(path), "alt": alt} for path, alt in self.images()],
        }

    def save_draft(self):
        data = self.draft()
        if not data["images"] and not data["caption"].strip():
            store.clear_draft()
            return
        store.save_draft(data)

    def restore_draft(self) -> int:
        """Put back whatever was staged when the window last closed."""
        data = store.load_draft()
        if not data:
            return 0
        restored = 0
        for entry in data.get("images", []):
            path = Path(entry.get("path", ""))
            if not path.exists():
                continue          # deleted since; silently skip rather than error
            self.add_paths([path])
            row = self.table.rowCount() - 1
            self.table.item(row, 2).setText(entry.get("alt") or "")
            restored += 1
        self.caption.setPlainText(data.get("caption") or "")
        index = self.visibility.findText(data.get("visibility") or "public")
        if index >= 0:
            self.visibility.setCurrentIndex(index)
        self.update_counter()
        return restored

    # -- actions ----------------------------------------------------------

    def do_post(self):
        images = self.gather()
        if images is None:
            return
        caption = self.caption.toPlainText()
        visibility = self.visibility.currentText()
        self.busy(True)
        self.status.emit("Uploading %d file(s) ..." % len(images))

        def work():
            return self.session.client().publish(images, caption, visibility)

        self.worker = Worker(work)
        self.worker.done.connect(self.post_ok)
        self.worker.failed.connect(self.post_bad)
        self.worker.start()

    def post_ok(self, result):
        self.busy(False)
        self.clear()
        self.posted.emit(result.get("url") or "(posted)")

    def post_bad(self, message):
        self.busy(False)
        self.status.emit("Failed")
        QMessageBox.critical(self, "pfpost", message)

    def do_queue(self):
        images = self.gather()
        if images is None:
            return
        when = self.when.dateTime().toPython()
        if when.tzinfo is None:
            when = when.astimezone()
        if when <= datetime.now(timezone.utc):
            if QMessageBox.question(
                    self, "pfpost",
                    "That time is in the past. Queue it to post on the next run?") \
                    != QMessageBox.Yes:
                return
        item = pfqueue.add(images, self.caption.toPlainText(),
                           self.visibility.currentText(), when)
        self.clear()
        self.queued.emit("Queued %s for %s"
                         % (item["id"], pfqueue.local_str(item["post_at"])))


class QueuePanel(QWidget):
    changed = Signal(str)

    def __init__(self, session: Session, parent=None, tokens: dict | None = None):
        super().__init__(parent)
        self.tokens = tokens or theme.tokens(dark=False)
        self.session = session
        self.worker = None

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["ID", "Post at", "Status", "Images", "Caption"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        run = QPushButton("Run due now")
        run.setProperty("accent", True)
        run.clicked.connect(self.run_due)
        self.clear_button = QPushButton("Clear posted")
        self.clear_button.setToolTip(
            "Remove finished items - posted and failed. Pending posts stay.")
        self.clear_button.clicked.connect(self.clear_posted)
        remove = QPushButton("Remove selected")
        remove.setToolTip("Remove the highlighted rows, cancelling them if pending")
        remove.clicked.connect(self.remove_selected)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)

        bar = QHBoxLayout()
        bar.addWidget(run)
        bar.addWidget(self.clear_button)
        bar.addWidget(remove)
        bar.addWidget(refresh)
        bar.addStretch()

        # Queued posts publish only when something runs the queue. Say so.
        self.runner_label = QLabel("Checking background posting ...")
        self.runner_button = QPushButton("Enable background posting")
        self.runner_button.clicked.connect(self.toggle_runner)
        self.runner_button.setEnabled(False)
        self.runner_registered = False
        self.runner_worker = None

        runner_bar = QHBoxLayout()
        runner_bar.addWidget(self.runner_label, 1)
        runner_bar.addWidget(self.runner_button)

        layout = QVBoxLayout(self)
        queue_heading = QLabel("Queue")
        queue_heading.setObjectName("sectionLabel")
        layout.addWidget(queue_heading)
        layout.addWidget(self.table)
        layout.addLayout(bar)
        layout.addLayout(runner_bar)
        # While the window is open there is no reason to wait for the background
        # task's interval: due items are published on the minute. The scheduled
        # task covers the app being closed.
        self.auto_timer = QTimer(self)
        self.auto_timer.setInterval(AUTO_RUN_MS)
        self.auto_timer.timeout.connect(self.auto_run)
        self.auto_timer.start()
        QTimer.singleShot(3000, self.auto_run)     # catch up shortly after launch

        self.reload()
        self.refresh_runner()

    def auto_run(self) -> str:
        """Publish anything due, quietly. Returns why it did or did not run."""
        if self.worker is not None and self.worker.isRunning():
            return "busy"
        try:
            if not self.session.status()["connected"]:
                return "disconnected"
        except Exception:
            return "disconnected"
        if not pfqueue.due():
            return "nothing due"
        self.run_due()
        return "running"

    # -- background runner ------------------------------------------------

    def refresh_runner(self):
        if not scheduler.available():
            self.runner_label.setText(
                "Background posting needs Windows - run `pfpost queue run` from cron "
                "or a timer instead.")
            self.runner_button.hide()
            return
        self.runner_worker = Worker(scheduler.status)
        self.runner_worker.done.connect(self.runner_state)
        self.runner_worker.failed.connect(
            lambda m: self.runner_state({"registered": False}))
        self.runner_worker.start()

    def runner_state(self, info: dict):
        self.runner_registered = bool(info.get("registered"))
        self.runner_button.setEnabled(True)
        if self.runner_registered:
            when = scheduler.describe_interval(info.get("interval", ""))
            extra = ""
            if info.get("last_result") not in (None, 0, 267011):
                extra = "  Last run reported code %s." % info["last_result"]
            self.runner_label.setText(
                "Background posting is on - the queue runs %s while pfpost is "
                "closed, and every minute while it is open.%s" % (when, extra))
            self.runner_label.setStyleSheet("")
            self.runner_button.setText("Disable background posting")
        else:
            self.runner_label.setText(
                "Background posting is off - due posts publish while pfpost is "
                "open, but nothing goes out once you close it.")
            self.runner_label.setStyleSheet("color:%s" % self.tokens["warning"])
            self.runner_button.setText("Enable background posting")

    def toggle_runner(self):
        if self.runner_registered:
            if QMessageBox.question(
                    self, "pfpost",
                    "Turn off background posting?\n\nQueued posts will then only "
                    "publish when you click Run due now.") != QMessageBox.Yes:
                return
            action, args = scheduler.unregister, ()
        else:
            minutes, ok = QInputDialog.getInt(
                self, "Enable background posting",
                "Check the queue every how many minutes?",
                scheduler.DEFAULT_INTERVAL, 1, 1440, 1)
            if not ok:
                return
            action, args = scheduler.register, (minutes,)

        self.runner_button.setEnabled(False)
        self.runner_label.setText("Updating Windows Task Scheduler ...")
        self.runner_worker = Worker(action, *args)
        self.runner_worker.done.connect(lambda _=None: self.refresh_runner())
        self.runner_worker.failed.connect(self.runner_failed)
        self.runner_worker.start()

    def runner_failed(self, message):
        self.refresh_runner()
        QMessageBox.critical(
            self, "pfpost",
            "Could not update the scheduled task.\n\n%s\n\n"
            "You can still publish with Run due now." % message)

    def reload(self):
        rows = pfqueue.items(include_done=True)
        self.table.setRowCount(len(rows))
        for r, item in enumerate(rows):
            caption = (item.get("caption") or "").replace("\n", " ")
            values = [item["id"], pfqueue.local_str(item["post_at"]), item["status"],
                      str(len(item["images"])), caption]
            for c, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if item["status"] == "failed":
                    cell.setToolTip(item.get("error", ""))
                self.table.setItem(r, c, cell)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)

        tally = pfqueue.counts()
        finished = tally.get("posted", 0) + tally.get("failed", 0)
        self.clear_button.setEnabled(finished > 0)
        self.clear_button.setText("Clear posted (%d)" % finished if finished
                                  else "Clear posted")

    def selected_ids(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(row, 0).text() for row in rows]

    def remove_selected(self):
        ids = self.selected_ids()
        if not ids:
            QMessageBox.information(
                self, "pfpost",
                "Select a row first, or use Clear posted to tidy up finished posts.")
            return
        pending = sum(1 for item in pfqueue.items(include_done=True)
                      if item["id"] in ids and item["status"] == "pending")
        if pending and QMessageBox.question(
                self, "pfpost",
                "Remove %d item%s?\n\n%d %s not been posted yet and will be "
                "cancelled." % (len(ids), "" if len(ids) == 1 else "s", pending,
                                "has" if pending == 1 else "have")) != QMessageBox.Yes:
            return
        removed = pfqueue.remove_many(ids)
        self.reload()
        self.changed.emit("Removed %d item%s." % (removed, "" if removed == 1 else "s"))

    def clear_posted(self):
        tally = pfqueue.counts()
        finished = tally.get("posted", 0) + tally.get("failed", 0)
        if not finished:
            self.changed.emit("Nothing finished to clear.")
            return
        removed = pfqueue.clear(("posted", "failed"))
        self.reload()
        self.changed.emit("Cleared %d finished item%s."
                          % (removed, "" if removed == 1 else "s"))

    def run_due(self):
        self.changed.emit("Running due posts ...")
        self.worker = Worker(pfqueue.run, self.session)
        self.worker.done.connect(self.ran)
        self.worker.failed.connect(self.ran_bad)
        self.worker.start()

    def ran(self, processed):
        self.reload()
        if not processed:
            self.changed.emit("Nothing due.")
            return
        posted = sum(1 for i in processed if i["status"] == "posted")
        failed = sum(1 for i in processed if i["status"] == "failed")
        self.changed.emit("Ran %d item(s): %d posted, %d failed."
                          % (len(processed), posted, failed))

    def ran_bad(self, message):
        self.changed.emit("Queue run failed.")
        QMessageBox.critical(self, "pfpost", message)


class MainWindow(QMainWindow):
    def __init__(self, tokens: dict | None = None):
        super().__init__()
        self.tokens = tokens or theme.tokens(dark=False)
        self.session = Session()
        self.worker = None
        self._runner_nudged = False
        self.setWindowTitle("pfpost")
        self.resize(880, 780)

        self.account_bar = AccountBar()
        self.account_bar.set_tokens(self.tokens)
        self.composer = Composer(self.session, tokens=self.tokens)
        self.queue_panel = QueuePanel(self.session, tokens=self.tokens)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.composer)
        splitter.addWidget(self.queue_panel)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.account_bar)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(central)

        self.account_bar.connect_requested.connect(self.connect_account)
        self.account_bar.disconnect_requested.connect(self.disconnect_account)
        self.composer.posted.connect(self.on_posted)
        self.composer.queued.connect(self.on_queued)
        self.composer.status.connect(self.say)
        self.queue_panel.changed.connect(self.say)

        self.connect_action = QAction("Connect account...", self)
        self.connect_action.triggered.connect(self.connect_account)
        self.disconnect_action = QAction("Disconnect", self)
        self.disconnect_action.triggered.connect(self.disconnect_account)

        menu = self.menuBar().addMenu("Account")
        menu.addAction(self.connect_action)
        menu.addAction(self.disconnect_action)

        self.statusBar().showMessage("Ready")
        self.refresh_account()

        pfqueue.prune_staged()      # tidy copies orphaned by an interrupted removal
        restored = self.composer.restore_draft()
        if restored or self.composer.caption.toPlainText().strip():
            self.say("Restored your unsent draft (%d image%s)."
                     % (restored, "" if restored == 1 else "s"))

    def closeEvent(self, event):
        self.composer.save_draft()
        super().closeEvent(event)

    def say(self, message: str):
        self.statusBar().showMessage(message, 12000)

    def on_posted(self, url: str):
        self.say("Posted: %s" % url)
        PostedDialog(url, self).exec()

    def on_queued(self, message: str):
        """Queueing is a silent no-op unless something runs the queue, so say so
        at the moment the expectation is formed rather than in a help menu."""
        self.say(message)
        self.queue_panel.reload()
        if self.queue_panel.runner_registered or self._runner_nudged:
            return
        if not scheduler.available():
            return
        self._runner_nudged = True     # ask once per session, not every time
        answer = QMessageBox.question(
            self, "Nothing will publish this yet",
            "%s.\n\nBackground posting is off, so this will only go out when you "
            "click Run due now with pfpost open.\n\nTurn on background posting?"
            % message,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if answer == QMessageBox.Yes:
            self.queue_panel.toggle_runner()

    def refresh_account(self):
        info = self.session.status()
        self.account_bar.show_state(info)
        self.disconnect_action.setEnabled(info["configured"])
        self.composer.set_enabled(info["connected"])
        if not info["connected"]:
            self.say("Not connected - use Account > Connect account")
            return
        self.say("Loading %s ..." % self.session.instance)

        def work():
            # One trip: instance limits, plus the account name when readable.
            limits = self.session.public_client().limits()
            username = None
            try:
                username = self.session.identity()
            except Exception:
                pass
            return limits, username

        self.worker = Worker(work)
        self.worker.done.connect(self.account_loaded)
        self.worker.failed.connect(self.account_failed)
        self.worker.start()

    def account_loaded(self, result):
        limits, username = result
        self.composer.set_limits(limits)
        self.account_bar.show_state(self.session.status(), username)
        detail = []
        if limits.get("max_media_attachments"):
            detail.append("max %d attachments" % limits["max_media_attachments"])
        if limits.get("max_characters"):
            detail.append("%d characters" % limits["max_characters"])
        self.say("Ready. " + ", ".join(detail) if detail else "Ready.")

    def account_failed(self, message):
        self.account_bar.show_state(self.session.status())
        self.say("Connected, but could not read instance details: %s" % message)

    def connect_account(self):
        dialog = ConnectDialog(self.session, self)
        if dialog.exec() == QDialog.Accepted:
            self.adopt(Session())

    def disconnect_account(self):
        info = self.session.status()
        if not info["configured"]:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Disconnect")
        box.setIcon(QMessageBox.Question)
        box.setText("Disconnect from %s?" % info["instance"])
        box.setInformativeText(
            "Credentials are removed from this machine.\n\n"
            "Pixelfed has no token-revocation endpoint, so the token stays valid "
            "on the server until you revoke it there:\n%s" % self.session.revoke_url())
        keep = box.addButton("Sign out, keep client", QMessageBox.AcceptRole)
        full = box.addButton("Remove everything", QMessageBox.DestructiveRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()

        clicked = box.clickedButton()
        if clicked not in (keep, full):
            return
        self.session.disconnect(forget_client=(clicked is full))
        self.adopt(Session())
        self.say("Disconnected from %s." % info["instance"])

    def adopt(self, session: Session):
        """Swap in a fresh session after connect/disconnect."""
        self.session = session
        self.composer.session = session
        self.queue_panel.session = session
        self.refresh_account()

def apply_theme(app) -> dict:
    """Paint the application in Pixelfed's palette and return the tokens."""
    tokens = theme.tokens(theme.is_dark(app))
    app.setStyleSheet(theme.stylesheet(tokens))
    return tokens


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("pfpost")
    app.setApplicationDisplayName("pfpost")
    try:
        app.setWindowIcon(theme.make_icon())
    except Exception:
        pass                       # an icon is not worth failing to start over

    tokens = apply_theme(app)
    window = MainWindow(tokens)
    window.show()
    if not window.session.status()["configured"]:
        window.connect_account()
    sys.exit(app.exec())
