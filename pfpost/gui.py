"""Desktop interface (PySide6).

Every network call runs on a worker thread; the UI thread only renders. All
logic lives in api/session/queue - this module is presentation only.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, QSize, Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from . import api, queue as pfqueue, store
from .session import AuthError, DEFAULT_PORT, Session

THUMB = 56
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
        self.buttons.button(QDialogButtonBox.Ok).setText("Connect")
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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.addWidget(self.dot)
        layout.addWidget(self.primary)
        layout.addWidget(self.detail, 1)
        layout.addWidget(self.button)
        self.apply_theme()

    # Blend toward the background for secondary text. Fixed palette roles such
    # as `mid` are not reliably legible across themes, so this derives the
    # colour from the theme actually in use. 0.55 keeps contrast at or above
    # 3.6:1 on light, dark and mid-grey palettes - see test_gui.py.
    DIM_MIX = 0.55

    def dim_colour(self) -> QColor:
        palette = self.palette()
        text = palette.color(QPalette.WindowText)
        back = palette.color(QPalette.Window)
        mix = self.DIM_MIX
        return QColor(
            round(text.red() * mix + back.red() * (1 - mix)),
            round(text.green() * mix + back.green() * (1 - mix)),
            round(text.blue() * mix + back.blue() * (1 - mix)),
        )

    def apply_theme(self):
        """Restyle for the current palette.

        setStyleSheet() itself emits a PaletteChange, which re-enters
        changeEvent - so this guards against recursion and does nothing when
        the derived colour has not actually changed.
        """
        if self._applying:
            return
        colour = self.dim_colour().name()
        if colour == self._applied_colour:
            return
        self._applying = True
        try:
            self._applied_colour = colour
            self.detail.setStyleSheet("color:%s" % colour)
            # Scoped to this widget so child labels and the button keep their
            # own backgrounds.
            self.setStyleSheet(
                "#accountBar{background:palette(alternate-base);"
                "border-bottom:1px solid %s;}" % colour)
        finally:
            self._applying = False

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
        self._connected = bool(info.get("connected"))
        if not info.get("configured"):
            self.dot.setStyleSheet("color:#c0392b")
            self.primary.setText("Not connected")
            self.detail.setText("Connect an account to start posting")
            self.button.setText("Connect account")
            return
        if not self._connected:
            self.dot.setStyleSheet("color:#e67e22")
            self.primary.setText("Not signed in")
            self.detail.setText("Registered on %s - authorization needed"
                                % info.get("instance", "?"))
            self.button.setText("Sign in")
            return

        self.dot.setStyleSheet("color:#27ae60")
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

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
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

        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd HH:mm")

        self.post_now = QPushButton("Post now")
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
        layout.addWidget(QLabel("Drag images here, or use Add images"))
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
        self.counter.setStyleSheet("color:#c0392b;font-weight:bold" if over else "")

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
        return images

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

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
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
        run.clicked.connect(self.run_due)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self.remove_selected)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.reload)

        bar = QHBoxLayout()
        bar.addWidget(run)
        bar.addWidget(remove)
        bar.addWidget(refresh)
        bar.addStretch()

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Queue"))
        layout.addWidget(self.table)
        layout.addLayout(bar)
        self.reload()

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

    def selected_id(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        if not rows:
            return None
        return self.table.item(next(iter(rows)), 0).text()

    def remove_selected(self):
        item_id = self.selected_id()
        if not item_id:
            return
        try:
            pfqueue.remove(item_id)
        except pfqueue.QueueError as exc:
            QMessageBox.warning(self, "pfpost", str(exc))
            return
        self.reload()
        self.changed.emit("Removed %s" % item_id)

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
    def __init__(self):
        super().__init__()
        self.session = Session()
        self.worker = None
        self.setWindowTitle("pfpost")
        self.resize(880, 780)

        self.account_bar = AccountBar()
        self.composer = Composer(self.session)
        self.queue_panel = QueuePanel(self.session)

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
        self.composer.queued.connect(self.say)
        self.composer.status.connect(self.say)
        self.queue_panel.changed.connect(self.say)

        self.connect_action = QAction("Connect account...", self)
        self.connect_action.triggered.connect(self.connect_account)
        self.disconnect_action = QAction("Disconnect", self)
        self.disconnect_action.triggered.connect(self.disconnect_account)
        schedule_action = QAction("Scheduling help", self)
        schedule_action.triggered.connect(self.scheduling_help)

        menu = self.menuBar().addMenu("Account")
        menu.addAction(self.connect_action)
        menu.addAction(self.disconnect_action)
        menu.addSeparator()
        menu.addAction(schedule_action)

        self.statusBar().showMessage("Ready")
        self.refresh_account()

    def say(self, message: str):
        self.statusBar().showMessage(message, 12000)

    def on_posted(self, url: str):
        self.say("Posted: %s" % url)
        QMessageBox.information(self, "pfpost", "Posted.\n\n%s" % url)

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

    def scheduling_help(self):
        script = Path(sys.argv[0]).resolve()
        python = Path(sys.executable).resolve()
        runner = python.with_name("pythonw.exe")
        runner = runner if runner.exists() else python
        QMessageBox.information(self, "Scheduling", (
            "The queue only publishes when something runs it.\n\n"
            "Register a Windows task that drains it every 15 minutes:\n\n"
            "$action = New-ScheduledTaskAction -Execute '%s' "
            "-Argument '\"%s\" queue run'\n"
            "$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) "
            "-RepetitionInterval (New-TimeSpan -Minutes 15)\n"
            "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable\n"
            "Register-ScheduledTask -TaskName 'Pixelfed Poster' -Action $action "
            "-Trigger $trigger -Settings $settings\n\n"
            "Or run `pfpost schedule` for the full command." % (runner, script)))


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("pfpost")
    window = MainWindow()
    window.show()
    if not window.session.status()["configured"]:
        window.connect_account()
    sys.exit(app.exec())
