"""Desktop interface (PySide6).

Every network call runs on a worker thread; the UI thread only renders. All
logic lives in api/session/queue - this module is presentation only.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, QMimeData, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices, QDrag, QIcon, QPixmap, QTextCharFormat
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QStackedWidget, QStyledItemDelegate, QToolButton,
    QVBoxLayout, QWidget,
)

from . import __version__, api, queue as pfqueue, scheduler, store, theme, updates
from .session import DEFAULT_PORT, Session

# How often an open window checks its own queue. The scheduled task
# handles the app being closed; this stops you waiting on its interval.
AUTO_RUN_MS = 60_000
IMAGE_FILTER = ("Media (*.png *.jpg *.jpeg *.gif *.webp *.avif *.heic *.mp4 *.mov);;"
                "All files (*)")


def mark_accent(button):
    """Give a button the accent style, even one that is already polished.

    Property selectors are resolved when a widget is first polished, and
    QMessageBox.addButton() polishes the button before returning it - so a
    plain setProperty() afterwards is silently ignored.
    """
    button.setProperty("accent", True)
    button.style().unpolish(button)
    button.style().polish(button)
    return button


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


# --------------------------------------------------------------------------
# small builders shared by the pages
# --------------------------------------------------------------------------

def styled_label(text: str, role: str | None = None) -> QLabel:
    label = QLabel(text)
    if role:
        label.setProperty("role", role)
    return label


def rounded_menu(parent) -> QMenu:
    """A QMenu whose window is transparent outside its rounded outline.

    Without this the stylesheet's border-radius draws over an opaque square,
    leaving light or dark corners poking out.
    """
    menu = QMenu(parent)
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint
                        | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    return menu


def card_frame() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    return line


def friendly_time(iso: str | None) -> str:
    """'2026-09-18T08:00:00+00:00' -> 'Fri, Sep 18 · 09:00' in local time."""
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso
    return "%s, %s %d · %s" % (when.strftime("%a"), when.strftime("%b"), when.day,
                               when.strftime("%H:%M"))


VISIBILITIES = (("public", "Public", "globe"), ("unlisted", "Unlisted", "unlisted"),
                ("private", "Followers only", "lock"))


# --------------------------------------------------------------------------
# top bar
# --------------------------------------------------------------------------

class TopBar(QWidget):
    """Logo, the Compose / Queue tabs, settings, and one quiet account chip.

    Everything the old account bar spelled out - instance, scopes, secret
    storage - sits behind Connection details in the chip's menu.
    """

    connect_requested = Signal()
    disconnect_requested = Signal()
    open_requested = Signal()
    details_requested = Signal()
    tab_changed = Signal(int)

    def __init__(self, tokens: dict | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedHeight(60)
        self.tokens = tokens or theme.tokens(dark=False)
        self._last_state: dict = {"configured": False, "connected": False}
        self._last_username = None

        logo = QLabel()
        logo.setPixmap(theme.rounded_pixmap(QPixmap.fromImage(theme.draw_icon(56)), 28, 28, 0))
        wordmark = QLabel("pfpost")
        wordmark.setObjectName("wordmark")

        self.segmented = QFrame()
        self.segmented.setObjectName("segmented")
        self.segmented.setFixedHeight(40)
        seg = QHBoxLayout(self.segmented)
        seg.setContentsMargins(3, 3, 3, 3)
        seg.setSpacing(2)
        self.compose_tab = QPushButton("Compose")
        self.queue_tab = QPushButton("Queue")
        self.tab_group = QButtonGroup(self)
        for index, button in enumerate((self.compose_tab, self.queue_tab)):
            button.setObjectName("tab")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            self.tab_group.addButton(button, index)
            seg.addWidget(button)
        self.compose_tab.setChecked(True)
        self.tab_group.idClicked.connect(self.tab_changed)

        # The pending count rides inside the Queue tab, as in the mockup.
        self.badge = QLabel("", self.queue_tab)
        self.badge.setObjectName("badge")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.badge.hide()
        self.queue_tab.installEventFilter(self)

        self.settings_button = QToolButton()
        self.settings_button.setObjectName("iconButton")
        self.settings_button.setToolTip("Settings")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setPopupMode(QToolButton.InstantPopup)
        self.settings_button.setCursor(Qt.PointingHandCursor)

        self.chip = QToolButton()
        self.chip.setObjectName("accountChip")
        self.chip.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.chip.setPopupMode(QToolButton.InstantPopup)
        self.chip.setIconSize(QSize(30, 30))
        self.chip.setCursor(Qt.PointingHandCursor)
        self.chip_menu = rounded_menu(self.chip)
        self.header_action = QAction("", self)
        self.header_action.setEnabled(False)
        self.open_action = QAction("Open account", self)
        self.open_action.triggered.connect(self.open_requested)
        self.details_action = QAction("Connection details", self)
        self.details_action.triggered.connect(self.details_requested)
        self.disconnect_action = QAction("Disconnect", self)
        self.disconnect_action.triggered.connect(self.disconnect_requested)
        for action in (self.header_action, None, self.open_action, self.details_action,
                       None, self.disconnect_action):
            if action is None:
                self.chip_menu.addSeparator()
            else:
                self.chip_menu.addAction(action)
        self.chip.setMenu(self.chip_menu)

        self.connect_button = QPushButton("Connect account")
        self.connect_button.setProperty("accent", True)
        self.connect_button.setProperty("size", "small")
        self.connect_button.clicked.connect(self.connect_requested)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)
        layout.addWidget(logo)
        layout.addWidget(wordmark)
        layout.addStretch(1)
        layout.addWidget(self.segmented, 0, Qt.AlignVCenter)
        layout.addStretch(1)
        layout.addWidget(self.settings_button)
        layout.addWidget(self.chip)
        layout.addWidget(self.connect_button)
        self.set_tokens(self.tokens)

    def set_tokens(self, tokens: dict):
        self.tokens = tokens
        colour = tokens["heading"]
        self.settings_button.setIcon(theme.svg_icon("sliders", colour))
        self.open_action.setIcon(theme.svg_icon("external", colour, 16))
        self.details_action.setIcon(theme.svg_icon("info", colour, 16))
        self.disconnect_action.setIcon(theme.svg_icon("logout", tokens["danger"], 16))
        self.show_state(self._last_state, self._last_username)

    def select_tab(self, index: int):
        self.tab_group.button(index).setChecked(True)
        self.tab_changed.emit(index)

    def set_pending(self, count: int):
        self.badge.setVisible(count > 0)
        self.badge.setText(str(count))
        # Selector required: a bare declaration cascades to the badge itself,
        # padding its digit out of sight.
        self.queue_tab.setStyleSheet(
            "QPushButton#tab { padding-right: 42px; }" if count > 0 else "")
        self._place_badge()

    def eventFilter(self, watched, event):
        # The tab's final size arrives after the bar's; place the badge then.
        if watched is self.queue_tab and event.type() == QEvent.Resize:
            self._place_badge()
        return super().eventFilter(watched, event)

    def _place_badge(self):
        # Measured from the digits: the label's size hint includes stylesheet
        # minimums and came out wide enough to cover the tab's own text.
        width = max(20, self.badge.fontMetrics().horizontalAdvance(self.badge.text()) + 12)
        tab = self.queue_tab
        self.badge.setGeometry(tab.width() - width - 12, (tab.height() - 20) // 2, width, 20)

    def show_state(self, info: dict, username: str | None = None):
        self._last_state, self._last_username = info, username
        connected = bool(info.get("connected"))
        self.chip.setVisible(connected)
        self.connect_button.setVisible(not connected)
        self.open_action.setEnabled(connected)
        self.disconnect_action.setEnabled(bool(info.get("configured")))
        if not info.get("configured"):
            self.connect_button.setText("Connect account")
            self.connect_button.setToolTip("Connect a Pixelfed account to start posting")
            return
        instance = info.get("instance") or "?"
        if not connected:
            self.connect_button.setText("Sign in")
            self.connect_button.setToolTip("Registered on %s - authorization needed" % instance)
            return
        who = "@%s" % username if username else instance
        self.chip.setText(" " + who)         # the style draws no gap after the icon
        self.chip.setIcon(QIcon(theme.avatar_pixmap(username or instance, 30)))
        self.chip.setToolTip("%s on %s" % (who, instance) if username else instance)
        self.header_action.setText("%s · %s" % (who, instance) if username else instance)

    def details_text(self) -> str:
        """Everything the old account bar printed, now on request only."""
        info, username = self._last_state, self._last_username
        lines = ["Instance: %s" % (info.get("instance") or "?")]
        if username:
            lines.append("Account: @%s" % username)
        elif info.get("connected") and not info.get("can_read"):
            lines.append("Account: shown after your first post (this token is write-only)")
        lines.append("Scopes: %s" % (info.get("scopes") or "?"))
        lines.append("Secrets stored in: %s" % (info.get("backend") or "?"))
        if info.get("expires_at"):
            lines.append("Token expires: %s" % pfqueue.local_str(info["expires_at"]))
        return "\n".join(lines)


# --------------------------------------------------------------------------
# compose
# --------------------------------------------------------------------------

class ImageTile(QWidget):
    """One image: a rounded thumbnail with a remove button, and its alt text."""

    removed = Signal(object)
    move_requested = Signal(object, int)
    dropped_on = Signal(object, object)
    DRAG_TYPE = "application/x-pfpost-image-tile"
    WIDTH, HEIGHT = 196, 140

    def __init__(self, path: Path, tokens: dict, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedWidth(self.WIDTH)
        self.setAcceptDrops(True)
        self._drag_start = None

        self.thumb = QLabel()
        self.thumb.setObjectName("thumb")
        self.thumb.setFixedSize(self.WIDTH, self.HEIGHT)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setToolTip(str(path))
        self.thumb.setCursor(Qt.OpenHandCursor)
        self.thumb.installEventFilter(self)
        pixmap = QPixmap(str(path))
        if pixmap.isNull():          # video, or a format Qt cannot decode
            self.thumb.setText(path.name)
            self.thumb.setWordWrap(True)
        else:
            self.thumb.setPixmap(theme.rounded_pixmap(pixmap, self.WIDTH, self.HEIGHT))

        self.remove_button = QToolButton(self.thumb)
        self.remove_button.setObjectName("tileRemove")
        self.remove_button.setIcon(theme.svg_icon("x", "#ffffff", 14, 2.2))
        self.remove_button.setToolTip("Remove %s" % path.name)
        self.remove_button.setAccessibleName("Remove image")
        self.remove_button.setCursor(Qt.PointingHandCursor)
        self.remove_button.move(self.WIDTH - 32, 6)
        self.remove_button.clicked.connect(lambda: self.removed.emit(self))

        caption = styled_label("Alt text", "hint")
        self.move_left = QToolButton()
        self.move_left.setText("←")
        self.move_left.setToolTip("Move image left")
        self.move_left.setAccessibleName("Move image left")
        self.move_left.clicked.connect(lambda: self.move_requested.emit(self, -1))
        self.move_right = QToolButton()
        self.move_right.setText("→")
        self.move_right.setToolTip("Move image right")
        self.move_right.setAccessibleName("Move image right")
        self.move_right.clicked.connect(lambda: self.move_requested.emit(self, 1))
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addWidget(caption)
        controls.addStretch()
        controls.addWidget(self.move_left)
        controls.addWidget(self.move_right)
        self.alt = QLineEdit()
        self.alt.setPlaceholderText("Describe this image")
        caption.setBuddy(self.alt)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self.thumb)
        layout.addLayout(controls)
        layout.addWidget(self.alt)

    def eventFilter(self, watched, event):
        if watched is self.thumb:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._drag_start = event.position().toPoint()
            elif event.type() == QEvent.MouseMove and self._drag_start is not None \
                    and event.buttons() & Qt.LeftButton:
                if (event.position().toPoint() - self._drag_start).manhattanLength() \
                        >= QApplication.startDragDistance():
                    self._drag_start = None
                    drag = QDrag(self)
                    data = QMimeData()
                    data.setData(self.DRAG_TYPE, b"tile")
                    drag.setMimeData(data)
                    drag.setPixmap(self.thumb.grab())
                    drag.exec(Qt.MoveAction)
                    return True
            elif event.type() == QEvent.MouseButtonRelease:
                self._drag_start = None
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.DRAG_TYPE) and isinstance(event.source(), ImageTile):
            event.acceptProposedAction()

    def dropEvent(self, event):
        source = event.source()
        if isinstance(source, ImageTile) and source is not self \
                and event.mimeData().hasFormat(self.DRAG_TYPE):
            self.dropped_on.emit(source, self)
            event.acceptProposedAction()

    def alt_text(self) -> str | None:
        return self.alt.text().strip() or None


class Composer(QWidget):
    """The Compose tab: images with alt text, caption, visibility, post/queue."""

    posted = Signal(str)
    queued = Signal(str)
    status = Signal(str)

    def __init__(self, session: Session, parent=None, tokens: dict | None = None):
        super().__init__(parent)
        self.tokens = tokens or theme.tokens(dark=False)
        self.session = session
        self.limits = {}
        self.worker = None
        self.tiles: list[ImageTile] = []
        self.setAcceptDrops(True)

        title = styled_label("New post", "title")
        hint = styled_label("Your draft is kept if you close pfpost", "hint")

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()

        self.add_tile = QPushButton(" Add images")
        self.add_tile.setObjectName("addTile")
        self.add_tile.setIcon(theme.svg_icon("plus", self.tokens["text"], 18))
        self.add_tile.setFixedSize(ImageTile.WIDTH, ImageTile.HEIGHT)
        self.add_tile.setToolTip("Or drop images anywhere on this tab")
        self.add_tile.setCursor(Qt.PointingHandCursor)
        self.add_tile.clicked.connect(self.browse)

        strip_host = QWidget()
        self.strip = QHBoxLayout(strip_host)
        self.strip.setContentsMargins(0, 0, 0, 0)
        self.strip.setSpacing(14)
        self.strip.addWidget(self.add_tile, 0, Qt.AlignTop)
        self.strip.addStretch(1)
        self.strip_area = QScrollArea()
        self.strip_area.setWidget(strip_host)
        self.strip_area.setWidgetResizable(True)
        self.strip_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.strip_area.setFrameShape(QFrame.NoFrame)
        self.strip_area.setFixedHeight(ImageTile.HEIGHT + 76)

        caption_label = styled_label("Caption", "label")
        self.caption = QPlainTextEdit()
        self.caption.setFixedHeight(104)
        self.caption.setPlaceholderText("Write a caption")
        caption_label.setBuddy(self.caption)
        self.caption.textChanged.connect(self.update_counter)
        self.counter = styled_label("0 characters", "hint")
        self.counter.setAlignment(Qt.AlignRight)

        visibility_label = styled_label("Visibility", "label")
        self.visibility = QComboBox()
        self.visibility.setObjectName("visibility")
        for value, text, icon in VISIBILITIES:
            self.visibility.addItem(theme.svg_icon(icon, self.tokens["heading"], 16), text, value)
        # The default combo delegate ignores ::item stylesheet rules, so the
        # popup rows would keep their cramped native look without this.
        self.visibility.setItemDelegate(QStyledItemDelegate(self.visibility))
        self.visibility.setMinimumWidth(170)
        visibility_label.setBuddy(self.visibility)

        self.when = QDateTimeEdit(QDateTime.currentDateTime().addSecs(3600))
        self.when.setCalendarPopup(True)
        # Qt paints weekends red by default; Pixelfed has no such convention.
        calendar = self.when.calendarWidget()
        for day in (Qt.Saturday, Qt.Sunday):
            calendar.setWeekdayTextFormat(day, QTextCharFormat())
        calendar.setVerticalHeaderFormat(calendar.VerticalHeaderFormat.NoVerticalHeader)
        self.when.setDisplayFormat("MMM d, yyyy · HH:mm")
        self.when.setMinimumWidth(236)
        self.when.setToolTip("When to post it, if you add it to the queue")

        self.post_now = QPushButton("Post now")
        self.post_now.setProperty("accent", True)
        self.post_now.clicked.connect(self.do_post)
        self.schedule = QPushButton("Add to queue")
        self.schedule.clicked.connect(self.do_queue)

        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(hint, 0, Qt.AlignBottom)

        visibility_row = QHBoxLayout()
        visibility_row.setSpacing(10)
        visibility_row.addWidget(visibility_label)
        visibility_row.addWidget(self.visibility)
        visibility_row.addStretch()

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addWidget(self.when)
        footer.addWidget(self.schedule)
        footer.addStretch()
        footer.addWidget(self.post_now)

        self.card = card_frame()
        inner = QVBoxLayout(self.card)
        inner.setContentsMargins(24, 24, 24, 20)
        inner.setSpacing(14)
        inner.addLayout(header)
        inner.addWidget(self.progress)
        inner.addWidget(self.strip_area)
        inner.addWidget(caption_label)
        inner.addWidget(self.caption)
        inner.addWidget(self.counter)
        inner.addLayout(visibility_row)
        inner.addStretch(1)
        inner.addWidget(divider())
        inner.addLayout(footer)

        self.card.setMaximumWidth(680)
        self.card.setMinimumWidth(560)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 24, 20, 8)
        outer.addStretch(1)
        outer.addWidget(self.card, 100)
        outer.addStretch(1)

    # -- drag and drop ----------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls()]
        self.add_paths([p for p in paths if p.is_file()])
        event.acceptProposedAction()

    # -- images -----------------------------------------------------------

    def browse(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add images", "", IMAGE_FILTER)
        self.add_paths([Path(f) for f in files])

    def add_paths(self, paths):
        for path in paths:
            tile = ImageTile(Path(path), self.tokens)
            tile.removed.connect(self.remove_tile)
            tile.move_requested.connect(self.move_tile)
            tile.dropped_on.connect(self.drop_tile)
            self.strip.insertWidget(len(self.tiles), tile, 0, Qt.AlignTop)
            self.tiles.append(tile)
        self.update_tile_controls()
        self.update_counter()

    def update_tile_controls(self):
        for index, tile in enumerate(self.tiles):
            tile.move_left.setEnabled(index > 0)
            tile.move_right.setEnabled(index < len(self.tiles) - 1)

    def move_tile(self, tile: ImageTile, offset: int):
        if tile not in self.tiles:
            return
        index = self.tiles.index(tile)
        target = index + offset
        if target < 0 or target >= len(self.tiles):
            return
        self.tiles.pop(index)
        self.tiles.insert(target, tile)
        self.strip.removeWidget(tile)
        self.strip.insertWidget(target, tile, 0, Qt.AlignTop)
        self.update_tile_controls()

    def drop_tile(self, source: ImageTile, target: ImageTile):
        if source in self.tiles and target in self.tiles and source is not target:
            self.move_tile(source, self.tiles.index(target) - self.tiles.index(source))

    def remove_tile(self, tile: ImageTile):
        if tile in self.tiles:
            self.tiles.remove(tile)
            self.strip.removeWidget(tile)
            tile.hide()
            tile.setParent(None)
            tile.deleteLater()
            self.update_tile_controls()

    def images(self):
        return [(tile.path, tile.alt_text()) for tile in self.tiles]

    def clear(self):
        for tile in list(self.tiles):
            self.remove_tile(tile)
        self.caption.setPlainText("")
        self.update_counter()
        store.clear_draft()      # the work left the composer; nothing to restore

    def visibility_value(self) -> str:
        return self.visibility.currentData() or "public"

    def set_visibility(self, value: str):
        index = self.visibility.findData(value)
        if index >= 0:
            self.visibility.setCurrentIndex(index)

    # -- state ------------------------------------------------------------

    def set_limits(self, limits: dict):
        self.limits = limits or {}
        self.update_counter()

    def update_counter(self):
        used = len(self.caption.toPlainText())
        cap = self.limits.get("max_characters")
        self.counter.setText("%d characters" % used if not cap else "%d / %d" % (used, cap))
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
        if not self.confirm_links(self.caption.toPlainText(), self.visibility_value()):
            return None
        return images

    def confirm_alt_text(self, images) -> bool:
        """Alt text cannot be added after posting, so ask before, not never."""
        missing = [path.name for path, alt in images if not (alt or "").strip()]
        if not missing or not store.load_prefs()["warn_alt_text"]:
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
        mark_accent(add)
        box.addButton("Post without it", QMessageBox.AcceptRole)
        box.setDefaultButton(add)
        mute = QCheckBox("Don't ask me about alt text again")
        box.setCheckBox(mute)
        box.exec()
        if mute.isChecked():
            store.set_pref("warn_alt_text", False)
        if box.clickedButton() is add:
            for tile in self.tiles:
                if not tile.alt_text():
                    self.strip_area.ensureWidgetVisible(tile)
                    tile.alt.setFocus()
                    break
            return False
        return True

    def confirm_links(self, caption: str, visibility: str) -> bool:
        """Warn that Pixelfed may quietly make a public post with a link unlisted.

        The post still succeeds and the server's reply still says public - the
        change happens afterwards - so without this the first sign is finding
        the post missing from your profile.
        """
        triggers = api.link_triggers(caption, visibility)
        if not triggers or not store.load_prefs()["warn_links"]:
            return True
        box = QMessageBox(self)
        box.setWindowTitle("Link in a public post")
        box.setIcon(QMessageBox.Warning)
        box.setText("Pixelfed may make this post unlisted.")
        box.setInformativeText(
            "Its spam filter changes public posts that contain links to "
            "unlisted when the account is under six months old or has 100 "
            "followers or fewer. The post still goes up, and you can appeal "
            "from the website.\n\nFound: %s"
            % ", ".join(triggers[:3]) + (" ..." if len(triggers) > 3 else ""))
        edit = box.addButton("Edit caption", QMessageBox.RejectRole)
        mark_accent(edit)
        box.addButton("Keep the link", QMessageBox.AcceptRole)
        box.setDefaultButton(edit)
        mute = QCheckBox("Don't warn me about links again")
        box.setCheckBox(mute)
        box.exec()
        if mute.isChecked():
            store.set_pref("warn_links", False)
        if box.clickedButton() is edit:
            # Select the first offending word so it can be deleted in one key.
            cursor = self.caption.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.caption.setTextCursor(cursor)
            self.caption.find(triggers[0])
            self.caption.setFocus()
            return False
        return True

    # -- drafts -----------------------------------------------------------

    def draft(self) -> dict:
        return {
            "caption": self.caption.toPlainText(),
            "visibility": self.visibility_value(),
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
            self.tiles[-1].alt.setText(entry.get("alt") or "")
            restored += 1
        self.caption.setPlainText(data.get("caption") or "")
        self.set_visibility(data.get("visibility") or "public")
        self.update_counter()
        return restored

    # -- actions ----------------------------------------------------------

    def do_post(self):
        images = self.gather()
        if images is None:
            return
        caption = self.caption.toPlainText()
        visibility = self.visibility_value()
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
        try:
            self.session.learn_from_status(result)
        except Exception:
            pass                # a convenience; never fail a published post over it
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
        item = pfqueue.add(images, self.caption.toPlainText(), self.visibility_value(), when)
        self.clear()
        self.queued.emit("Queued for %s" % friendly_time(item["post_at"]))


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------

class QueueRow(QFrame):
    """One queued post: thumbnail, caption, when and where, status, one action."""

    remove_requested = Signal(str)
    open_requested = Signal(str)

    def __init__(self, item: dict, tokens: dict, first: bool, parent=None):
        super().__init__(parent)
        self.item = item
        self.setObjectName("queueRow")
        self.setProperty("first", first)
        status = item.get("status", "pending")

        thumb = QLabel()
        thumb.setObjectName("thumb")
        thumb.setFixedSize(72, 72)
        thumb.setAlignment(Qt.AlignCenter)
        images = item.get("images") or []
        pixmap = QPixmap(images[0]["path"]) if images else QPixmap()
        if pixmap.isNull():
            # Posted items' staged copies are deleted; show a neutral tile.
            thumb.setPixmap(theme.svg_pixmap("image", tokens["muted"], 26))
        else:
            thumb.setPixmap(theme.rounded_pixmap(pixmap, 72, 72))

        caption = (item.get("caption") or "").strip().replace("\n", " ") or "(no caption)"
        self.title = styled_label("", "rowTitle")
        self.title.setText(self.title.fontMetrics().elidedText(caption, Qt.ElideRight, 360))
        self.title.setToolTip(caption if len(caption) > 40 else "")

        meta = QHBoxLayout()
        meta.setSpacing(12)
        if status == "posted":
            when = "Posted " + friendly_time(item.get("posted_at") or item.get("post_at"))
            when_icon = "check"
        else:
            when, when_icon = friendly_time(item.get("post_at")), "clock"
        visibility = next((v for v in VISIBILITIES if v[0] == item.get("visibility")),
                          VISIBILITIES[0])
        count = len(images)
        for icon, text in ((when_icon, when), (visibility[2], visibility[1]),
                           (None, "%d image%s" % (count, "" if count == 1 else "s"))):
            group = QHBoxLayout()
            group.setSpacing(5)
            if icon:
                glyph = QLabel()
                glyph.setPixmap(theme.svg_pixmap(icon, tokens["muted"], 14))
                group.addWidget(glyph)
            group.addWidget(styled_label(text, "meta"))
            meta.addLayout(group)
        meta.addStretch()

        column = QVBoxLayout()
        column.setSpacing(5)
        column.addWidget(self.title)
        column.addLayout(meta)

        self.pill = QLabel({"pending": "Pending", "posted": "Posted",
                            "failed": "Failed"}.get(status, status.title()))
        self.pill.setProperty("pill", status if status in ("pending", "posted", "failed")
                              else "pending")
        if status == "failed" and item.get("error"):
            self.pill.setToolTip(item["error"])

        self.action = QToolButton()
        self.action.setObjectName("iconButton")
        self.action.setCursor(Qt.PointingHandCursor)
        url = item.get("result_url") or ""
        if status == "posted" and url.startswith(("http://", "https://")):
            self.action.setIcon(theme.svg_icon("external", tokens["text"]))
            self.action.setToolTip("Open post")
            self.action.setAccessibleName("Open post")
            self.action.clicked.connect(lambda: self.open_requested.emit(url))
        else:
            self.action.setIcon(theme.svg_icon("trash", tokens["text"]))
            label = "Remove from queue" if status == "pending" else "Remove"
            self.action.setToolTip(label)
            self.action.setAccessibleName(label)
            self.action.clicked.connect(lambda: self.remove_requested.emit(item["id"]))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 14, 0, 14)
        layout.setSpacing(16)
        layout.addWidget(thumb)
        layout.addLayout(column, 1)
        layout.addWidget(self.pill, 0, Qt.AlignVCenter)
        layout.addWidget(self.action, 0, Qt.AlignVCenter)


class QueuePanel(QWidget):
    """The Queue tab: queued and posted items, and background posting."""

    changed = Signal(str)
    pending_changed = Signal(int)
    runner_summary = Signal(str, str, str)   # text, text colour, dot colour

    def __init__(self, session: Session, parent=None, tokens: dict | None = None):
        super().__init__(parent)
        self.tokens = tokens or theme.tokens(dark=False)
        self.session = session
        self.worker = None
        self.rows: list[QueueRow] = []

        title = styled_label("Queue", "title")
        self.subtitle = styled_label("", "hint")
        heading = QVBoxLayout()
        heading.setSpacing(2)
        heading.addWidget(title)
        heading.addWidget(self.subtitle)

        self.clear_button = QPushButton("Clear posted")
        self.clear_button.setProperty("flat", True)
        self.clear_button.setToolTip(
            "Remove finished items - posted and failed. Pending posts stay.")
        self.clear_button.clicked.connect(self.clear_posted)
        self.run_button = QPushButton(" Run due now")
        self.run_button.setProperty("size", "small")
        self.run_button.setIcon(theme.svg_icon("play", self.tokens["heading"], 16))
        self.run_button.setToolTip("Publish everything that is due right now")
        self.run_button.clicked.connect(self.run_due)

        top = QHBoxLayout()
        top.addLayout(heading, 1)
        top.addWidget(self.clear_button)
        top.addWidget(self.run_button)

        host = QWidget()
        self.list_layout = QVBoxLayout(host)
        self.list_layout.setContentsMargins(0, 0, 8, 0)
        self.list_layout.setSpacing(0)
        self.empty = styled_label("Nothing queued. Posts you add to the queue appear here.",
                                  "muted")
        self.empty.setAlignment(Qt.AlignCenter)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        # Queued posts publish only when something runs the queue. Say so.
        self.runner_dot = QLabel()
        self.runner_dot.setFixedSize(8, 8)
        self.runner_label = QLabel("Checking background posting ...")
        # Wrap: the broken-task message carries a full program path, and an
        # unwrapped label forces its minimum width onto the whole window.
        self.runner_label.setWordWrap(True)
        self.runner_button = QPushButton("Turn on")
        self.runner_button.setProperty("size", "small")
        self.runner_button.clicked.connect(self.runner_button_clicked)
        self.runner_button.setEnabled(False)
        self.runner_registered = False
        self.runner_broken = False
        self.runner_minutes = scheduler.DEFAULT_INTERVAL
        self.runner_worker = None

        runner_bar = QHBoxLayout()
        runner_bar.setSpacing(8)
        runner_bar.addWidget(self.runner_dot, 0, Qt.AlignVCenter)
        runner_bar.addWidget(self.runner_label, 1)
        runner_bar.addWidget(self.runner_button)

        card = card_frame()
        inner = QVBoxLayout(card)
        inner.setContentsMargins(24, 24, 24, 18)
        inner.setSpacing(10)
        inner.addLayout(top)
        inner.addWidget(scroll, 1)
        inner.addWidget(divider())
        inner.addLayout(runner_bar)
        card.setMaximumWidth(680)
        card.setMinimumWidth(560)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(20, 24, 20, 8)
        outer.addStretch(1)
        outer.addWidget(card, 100)
        outer.addStretch(1)

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
                "Background posting needs Task Scheduler, launchd, systemd or cron, "
                "and none was found - run `pfpost queue run` on a timer instead.")
            self.runner_button.hide()
            return
        self.runner_worker = Worker(scheduler.status)
        self.runner_worker.done.connect(self.runner_state)
        self.runner_worker.failed.connect(
            lambda m: self.runner_state({"registered": False}))
        self.runner_worker.start()

    def _runner_look(self, text: str, colour_key: str, dot_key: str, tooltip: str = ""):
        colour = self.tokens[colour_key] if colour_key else ""
        self.runner_label.setText(text)
        self.runner_label.setToolTip(tooltip)
        self.runner_label.setStyleSheet("color:%s" % colour if colour else "")
        self.runner_dot.setStyleSheet("background:%s; border-radius:4px;" % self.tokens[dot_key])
        self.runner_summary.emit(text, colour, self.tokens[dot_key])

    def runner_state(self, info: dict):
        self.runner_registered = bool(info.get("registered"))
        self.runner_broken = bool(self.runner_registered and info.get("broken"))
        self.runner_minutes = (scheduler.interval_minutes(info.get("interval", ""))
                               or scheduler.DEFAULT_INTERVAL)
        self.runner_button.setEnabled(True)
        mark = self.runner_button.setProperty
        if self.runner_broken:
            # The task exists but cannot start - e.g. it runs a Python that was
            # uninstalled. Windows only reports 0x80070002, which means nothing
            # to a person, and nothing posts until it is re-pointed.
            program = info.get("program") or "its program"
            self._runner_look(
                "Background posting is broken - the program it runs is missing "
                "(%s), so nothing posts while pfpost is closed. Repair points it "
                "at this copy of pfpost." % program, "danger", "danger")
            self.runner_button.setText("Repair")
            mark("accent", True)
        elif self.runner_registered:
            minutes = self.runner_minutes
            every = "%d min" % minutes if minutes < 60 or minutes % 60 else \
                "%d h" % (minutes // 60)
            extra = ""
            if info.get("last_result") not in (None, 0, 267011):
                extra = " · last run reported code %s" % info["last_result"]
            self._runner_look(
                "Background posting on · every %s%s" % (every, extra), "", "success",
                "The queue runs %s while pfpost is closed, and every minute while "
                "it is open." % scheduler.describe_interval(info.get("interval", "")))
            self.runner_button.setText("Change")
            mark("accent", False)
        else:
            self._runner_look(
                "Background posting off · nothing posts once you close pfpost",
                "", "danger",
                "Due posts publish while pfpost is open. Turn on background posting "
                "to publish them when it is closed too.")
            self.runner_button.setText("Turn on")
            mark("accent", True)
        self.runner_button.style().unpolish(self.runner_button)
        self.runner_button.style().polish(self.runner_button)

    def runner_button_clicked(self):
        if self.runner_registered and not self.runner_broken:
            menu = rounded_menu(self)
            menu.addAction("Check every ...", self.change_interval)
            menu.addAction("Turn off background posting", self.toggle_runner)
            menu.exec(self.runner_button.mapToGlobal(self.runner_button.rect().bottomLeft()))
        else:
            self.toggle_runner()

    def change_interval(self):
        minutes, ok = QInputDialog.getInt(
            self, "Background posting", "Check the queue every how many minutes?",
            self.runner_minutes, 1, 1440, 1)
        if ok:
            self._update_runner(scheduler.register, (minutes,))

    def toggle_runner(self):
        if self.runner_broken:
            # Re-register in place, keeping the interval the user chose.
            action, args = scheduler.register, (self.runner_minutes,)
        elif self.runner_registered:
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
        self._update_runner(action, args)

    def _update_runner(self, action, args):
        self.runner_button.setEnabled(False)
        self.runner_label.setText("Updating %s ..." % scheduler.backend_label())
        self.runner_worker = Worker(action, *args)
        self.runner_worker.done.connect(lambda _=None: self.refresh_runner())
        self.runner_worker.failed.connect(self.runner_failed)
        self.runner_worker.start()

    def runner_failed(self, message):
        self.refresh_runner()
        QMessageBox.critical(
            self, "pfpost",
            "Could not update background posting.\n\n%s\n\n"
            "You can still publish with Run due now." % message)

    # -- list -------------------------------------------------------------

    def reload(self):
        for row in self.rows:
            # Hide and detach now: deleteLater alone leaves the old row painted
            # under its replacement until the event loop gets round to it.
            self.list_layout.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        items = pfqueue.items(include_done=True)
        # Pending first, soonest first; then finished, newest first.
        pending = [i for i in items if i["status"] == "pending"]
        done = sorted((i for i in items if i["status"] != "pending"),
                      key=lambda i: i.get("posted_at") or i["post_at"], reverse=True)
        for index, item in enumerate(pending + done):
            row = QueueRow(item, self.tokens, first=index == 0)
            row.remove_requested.connect(self.remove_item)
            row.open_requested.connect(self.open_post)
            self.list_layout.insertWidget(index, row)
            self.rows.append(row)
        self.empty.setVisible(not self.rows)

        tally = pfqueue.counts()
        finished = tally.get("posted", 0) + tally.get("failed", 0)
        bits = ["%d pending" % tally.get("pending", 0), "%d posted" % tally.get("posted", 0)]
        if tally.get("failed"):
            bits.append("%d failed" % tally["failed"])
        self.subtitle.setText(" · ".join(bits))
        self.clear_button.setEnabled(finished > 0)
        self.clear_button.setText("Clear posted (%d)" % finished if finished
                                  else "Clear posted")
        self.pending_changed.emit(tally.get("pending", 0))

    def remove_item(self, item_id: str):
        item = next((i for i in pfqueue.items(include_done=True) if i["id"] == item_id), None)
        if item is None:
            self.reload()
            return
        if item["status"] == "pending":
            caption = (item.get("caption") or "this post").strip().splitlines()[0][:60]
            if QMessageBox.question(
                    self, "pfpost",
                    "Remove \"%s\" from the queue?\n\nIt has not been posted yet and "
                    "will be cancelled." % caption) != QMessageBox.Yes:
                return
        removed = pfqueue.remove_many([item_id])
        self.reload()
        self.changed.emit("Removed %d item%s." % (removed, "" if removed == 1 else "s"))

    def open_post(self, url: str):
        if url.startswith(("http://", "https://")):
            QDesktopServices.openUrl(QUrl(url))

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


# --------------------------------------------------------------------------
# window
# --------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, tokens: dict | None = None):
        super().__init__()
        self.tokens = tokens or theme.tokens(dark=False)
        self.session = Session()
        self.worker = None
        self._runner_nudged = False
        self.setWindowTitle("pfpost")
        self.resize(900, 780)
        self.setMinimumSize(640, 600)

        self.top_bar = TopBar(self.tokens)
        self.composer = Composer(self.session, tokens=self.tokens)
        self.queue_panel = QueuePanel(self.session, tokens=self.tokens)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.composer)
        self.stack.addWidget(self.queue_panel)

        # One quiet line under the pages, as wide as the cards: background
        # posting on the left (the Queue tab carries its own, with controls),
        # the version and the update check on the right. A transient message
        # borrows the left side, then gives it back.
        self.summary_dot = QLabel()
        self.summary_dot.setFixedSize(8, 8)
        # A button, not a label: clicking the status is the quickest way to flip
        # it (the Queue tab's line keeps the fuller Change menu).
        self.summary = QPushButton("")
        self.summary.setObjectName("statusLink")
        self.summary.setCursor(Qt.PointingHandCursor)
        self.summary.clicked.connect(self.summary_clicked)
        self.message = styled_label("", "hint")
        self.message.hide()
        self.message_timer = QTimer(self)
        self.message_timer.setSingleShot(True)
        self.message_timer.timeout.connect(self.clear_message)
        self.version_label = styled_label("pfpost %s" % __version__, "hint")
        self.update_button = QPushButton("Check for updates")
        self.update_button.setProperty("flat", True)
        self.update_button.setToolTip("Asks GitHub for the latest release. Nothing is "
                                      "checked unless you click.")
        self.update_button.clicked.connect(self.update_button_clicked)
        self.update_url = None
        self.update_worker = None

        footer_host = QWidget()
        footer_host.setMaximumWidth(680)
        footer_row = QHBoxLayout(footer_host)
        footer_row.setContentsMargins(4, 0, 0, 0)
        footer_row.setSpacing(8)
        footer_row.addWidget(self.summary_dot, 0, Qt.AlignVCenter)
        footer_row.addWidget(self.summary)
        footer_row.addWidget(self.message, 1)
        footer_row.addStretch(1)
        footer_row.addWidget(self.version_label)
        footer_row.addWidget(styled_label("·", "hint"))
        footer_row.addWidget(self.update_button)
        footer = QHBoxLayout()
        footer.setContentsMargins(20, 0, 20, 8)
        footer.addStretch(1)
        footer.addWidget(footer_host, 100)
        footer.addStretch(1)

        central = QWidget()
        central.setObjectName("page")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.top_bar)
        layout.addWidget(self.stack, 1)
        layout.addLayout(footer)
        self.setCentralWidget(central)

        self.top_bar.tab_changed.connect(self.show_tab)
        self.top_bar.connect_requested.connect(self.connect_account)
        self.top_bar.disconnect_requested.connect(self.disconnect_account)
        self.top_bar.open_requested.connect(self.open_account)
        self.top_bar.details_requested.connect(self.show_connection_details)
        # A post, from either tab, is how a write-only token learns its name.
        self.queue_panel.changed.connect(lambda _message: self.sync_account_name())
        self.queue_panel.pending_changed.connect(self.top_bar.set_pending)
        self.queue_panel.runner_summary.connect(self.show_runner_summary)
        self.composer.posted.connect(self.on_posted)
        self.composer.queued.connect(self.on_queued)
        self.composer.status.connect(self.say)
        self.queue_panel.changed.connect(self.say)
        self.top_bar.set_pending(pfqueue.counts().get("pending", 0))

        self.open_action = self.top_bar.open_action
        self.disconnect_action = self.top_bar.disconnect_action

        # Settings: also the way back after ticking "don't warn me again".
        self.warn_alt_action = QAction("Ask about missing alt text", self)
        self.warn_alt_action.setCheckable(True)
        self.warn_alt_action.toggled.connect(
            lambda on: store.set_pref("warn_alt_text", on))
        self.warn_links_action = QAction("Warn about links in public posts", self)
        self.warn_links_action.setCheckable(True)
        self.warn_links_action.toggled.connect(
            lambda on: store.set_pref("warn_links", on))
        self.check_updates_action = QAction("Check for updates", self)
        self.check_updates_action.triggered.connect(self.check_for_updates)
        settings = rounded_menu(self)
        settings.addAction(self.warn_alt_action)
        settings.addAction(self.warn_links_action)
        settings.addSeparator()
        settings.addAction(self.check_updates_action)
        settings.aboutToShow.connect(self.sync_options)
        self.top_bar.settings_button.setMenu(settings)
        self.sync_options()

        self.refresh_account()

        pfqueue.prune_staged()      # tidy copies orphaned by an interrupted removal
        restored = self.composer.restore_draft()
        if restored or self.composer.caption.toPlainText().strip():
            self.say("Restored your unsent draft (%d image%s)."
                     % (restored, "" if restored == 1 else "s"))

    def closeEvent(self, event):
        self.composer.save_draft()
        super().closeEvent(event)

    def show_tab(self, index: int):
        self.stack.setCurrentIndex(index)
        self._sync_footer()

    def _sync_footer(self):
        messaging = self.message_timer.isActive()
        on_compose = self.stack.currentIndex() == 0
        self.message.setVisible(messaging)
        self.summary.setVisible(on_compose and not messaging)
        self.summary_dot.setVisible(on_compose and not messaging)

    def clear_message(self):
        self.message.setText("")
        self._sync_footer()

    # -- updates ----------------------------------------------------------

    def update_button_clicked(self):
        if self.update_url:
            QDesktopServices.openUrl(QUrl(self.update_url))
        else:
            self.check_for_updates()

    def check_for_updates(self):
        if self.update_worker is not None and self.update_worker.isRunning():
            return
        self.update_button.setEnabled(False)
        self.update_button.setText("Checking ...")
        self.update_worker = Worker(updates.check)
        self.update_worker.done.connect(self.update_result)
        self.update_worker.failed.connect(self.update_failed)
        self.update_worker.start()

    def update_result(self, info: dict):
        self.update_button.setEnabled(True)
        if not info.get("newer"):
            self.update_url = None
            self.update_button.setText("Check for updates")
            self.say(updates.describe(info))
            return
        self.update_url = info["url"]
        self.update_button.setText("Update available: %s" % info["latest"])
        self.update_button.setStyleSheet("color:%s; font-weight:700;" % self.tokens["primary"])
        answer = QMessageBox.question(
            self, "Update available",
            "pfpost %s is available - you have %s.\n\nOpen the download page?"
            % (info["latest"], info["current"]))
        if answer == QMessageBox.Yes:
            QDesktopServices.openUrl(QUrl(self.update_url))

    def update_failed(self, message: str):
        self.update_button.setEnabled(True)
        self.update_button.setText("Check for updates")
        self.say("Couldn't check for updates: %s" % message)

    def show_runner_summary(self, text: str, colour: str, dot: str):
        # The broken message names a full path; the footer only needs the gist.
        panel = self.queue_panel
        if panel.runner_broken:
            text, tip = "Background posting is broken - click to repair", \
                "Points the task at this copy of pfpost, keeping its interval"
        elif panel.runner_registered:
            tip = "Click to turn off background posting"
        else:
            tip = "Click to turn on background posting"
        self.summary.setText(text)
        self.summary.setToolTip(tip)
        # Scoped, so hover's colour still applies when no override is set.
        self.summary.setStyleSheet("QPushButton#statusLink { color:%s; }" % colour
                                   if colour else "")
        self.summary_dot.setStyleSheet("background:%s; border-radius:4px;" % dot)

    def summary_clicked(self):
        # toggle_runner already asks before turning off, asks for an interval
        # before turning on, and repairs a broken task in place.
        self.queue_panel.toggle_runner()

    def sync_options(self):
        """Read prefs from disk - the warning dialog can change them too."""
        prefs = store.load_prefs()
        for action, key in ((self.warn_alt_action, "warn_alt_text"),
                            (self.warn_links_action, "warn_links")):
            action.blockSignals(True)
            action.setChecked(prefs[key])
            action.blockSignals(False)

    def say(self, message: str):
        if not message:
            self.message_timer.stop()
            self.clear_message()
            return
        self.message.setText(message)
        self.message_timer.start(12000)
        self._sync_footer()

    def open_account(self):
        if not self.session.status()["connected"]:
            return
        url = self.session.profile_url()
        # The instance name came from user input; only ever hand the OS a web URL.
        if not url.startswith("https://"):
            return
        QDesktopServices.openUrl(QUrl(url))
        self.say("Opened %s" % url)

    def show_connection_details(self):
        QMessageBox.information(self, "Connection details", self.top_bar.details_text())

    def sync_account_name(self):
        """Show a username learned from a post. No network."""
        name = self.session.state.get("username")
        if name and name != self.top_bar._last_username:
            self.top_bar.show_state(self.session.status(), name)

    def on_posted(self, url: str):
        self.sync_account_name()
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
        self.top_bar.show_state(info, self.session.state.get("username"))
        self.composer.set_enabled(info["connected"])
        if not info["connected"]:
            self.say("Not connected - use Connect account at the top right")
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
        self.top_bar.show_state(self.session.status(), username)
        detail = []
        if limits.get("max_media_attachments"):
            detail.append("max %d images" % limits["max_media_attachments"])
        if limits.get("max_characters"):
            detail.append("%d characters" % limits["max_characters"])
        self.say("Ready. " + ", ".join(detail) if detail else "Ready.")

    def account_failed(self, message):
        self.top_bar.show_state(self.session.status(), self.session.state.get("username"))
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
    try:
        tokens["check_image"] = theme.check_image(tokens["primary_text"])
    except OSError:
        pass                    # no temp dir: a filled box still reads as ticked
    try:
        for direction, key in theme.ARROW_DIRECTIONS:
            tokens[key] = theme.chevron_image(tokens["text"], direction)
    except OSError:
        pass                    # without them the native dropdown styling is kept
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
    try:
        theme.install_font(app)
    except Exception:
        pass                       # nor is a font; the platform font remains

    tokens = apply_theme(app)
    window = MainWindow(tokens)
    window.show()
    if not window.session.status()["configured"]:
        window.connect_account()
    sys.exit(app.exec())
