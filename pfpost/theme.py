"""Visual theme, built from Pixelfed's own palette.

Colours come from gram.social's signed-in web app (its spa.css), not the older
app.css an earlier version used - that is most of what made pfpost not feel
like gram.social:

    light   page #f3f4f6   cards #ffffff   borders #dee2e6
    dark    page #000000   cards #161618   raised  #212124   (pfpost: charcoal, below)
    primary #3b82f6        brand cyan #10c5f8 (small accents only)

Deliberate departures, each measured:

    primary   #2563eb  white text on #3b82f6 is 3.7:1, under 4.5
    muted     #64748b  (light) spa.css's #94a3b8 is 2.6:1 on white
    input_border       spa.css's field borders are ~1.2:1 - invisible, the
                       exact complaint that prompted 1.0.4's outline fix

Everything here is data, one string builder and a few pixmap helpers; no widget
logic, so the tokens can be asserted in tests without a window.
"""

from __future__ import annotations

LIGHT = {
    "name": "light",
    "accent": "#10c5f8",        # brand cyan - avatar, pending dots, the icon
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "primary_text": "#ffffff",
    "bg": "#f3f4f6",
    "surface": "#ffffff",
    "surface_alt": "#f8f9fa",
    "seg": "#f3f4f6",           # the tab strip's track
    "border": "#dee2e6",
    "border_strong": "#ced4da",
    "input_border": "#80868c",  # >= 3.3:1 on every light ground
    "heading": "#111827",
    "text": "#374151",
    "muted": "#64748b",
    "danger": "#dc2626",
    "success": "#16a34a",
    "warning": "#b45309",
    "selection": "#dbeafe",
    "selection_text": "#1e3a8a",
    "pending_bg": "#ecfeff",
    "pending_fg": "#0e7490",
    "done_bg": "#f1f5f9",
    "done_fg": "#475569",
    "failed_bg": "#fef2f2",
    "failed_fg": "#b91c1c",
    "overlay": "rgba(17, 24, 39, 158)",
}

DARK = {
    "name": "dark",
    "accent": "#10c5f8",
    "primary": "#2563eb",
    "primary_hover": "#3b82f6",
    "primary_text": "#ffffff",
    # Charcoal rather than gram.social's pure black, which read as a hole in
    # the screen. Cards, raised surfaces and borders step up from it together.
    "bg": "#16171b",
    "surface": "#1f2025",
    "surface_alt": "#2a2b31",
    "seg": "#2a2b31",
    "border": "#2e3036",
    "border_strong": "#3a3c43",
    "input_border": "#7a7c85",  # >= 3:1 on every dark ground
    "heading": "#e5e7eb",
    "text": "#9ca3af",
    "muted": "#8a8a8a",
    "danger": "#f87171",
    "success": "#4ade80",
    "warning": "#fbbf24",
    "selection": "#1e3a5f",
    "selection_text": "#e5e7eb",
    "pending_bg": "#0b2a31",
    "pending_fg": "#67e8f9",
    "done_bg": "#2a2b31",
    "done_fg": "#9ca3af",
    "failed_bg": "#2a1215",
    "failed_fg": "#fca5a5",
    "overlay": "rgba(0, 0, 0, 178)",
}


def tokens(dark: bool) -> dict:
    return dict(DARK if dark else LIGHT)


def stylesheet(t: dict) -> str:
    """Qt stylesheet for the whole application."""
    return """
/* No blanket QWidget background: a card's children must stay transparent on
   the card, and an id rule strong enough to force that would also beat the
   accent button's colour. Surfaces are painted by name instead. */
QWidget { color: %(text)s; }
QMainWindow, QDialog, QMessageBox, QInputDialog { background: %(bg)s; }
QWidget#page { background: %(bg)s; }
QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }

QFrame#card {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: 18px;
}
QFrame#divider { background: %(border)s; min-height: 1px; max-height: 1px; border: none; }

QWidget#topBar { background: %(surface)s; border-bottom: 1px solid %(border)s; }
QLabel#wordmark { font-size: 17px; font-weight: 700; color: %(heading)s; }

QLabel { background: transparent; }
QLabel[role="title"] { font-size: 22px; font-weight: 700; color: %(heading)s; }
QLabel[role="label"] { font-size: 13px; font-weight: 600; color: %(heading)s; }
QLabel[role="hint"] { font-size: 12px; color: %(muted)s; }
QLabel[role="muted"] { color: %(muted)s; }
QLabel[role="rowTitle"] { font-size: 15px; font-weight: 600; color: %(heading)s; }
QLabel[role="meta"] { font-size: 13px; color: %(muted)s; }

QMenu {
    background: %(surface)s;
    border: 1px solid %(input_border)s;
    border-radius: 14px;
    padding: 6px;
}
QMenu::item { padding: 9px 22px 9px 12px; border-radius: 8px; color: %(heading)s; }
QMenu::item:selected { background: %(surface_alt)s; color: %(heading)s; }
QMenu::item:disabled { color: %(muted)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 8px; }
QMenu::icon { padding-left: 8px; }
QMenu::indicator { width: 16px; height: 16px; left: 8px; }

/* Pill buttons, as on gram.social. */
QPushButton {
    background: %(surface)s;
    border: 1px solid %(input_border)s;
    border-radius: 19px;
    padding: 0 16px;
    min-height: 38px;
    color: %(heading)s;
    font-weight: 600;
}
QPushButton:hover { background: %(surface_alt)s; border-color: %(primary)s; }
QPushButton:pressed { background: %(selection)s; }
QPushButton:disabled { color: %(muted)s; border-color: %(border)s; background: %(surface)s; }
QPushButton:focus { outline: none; border-color: %(primary)s; }
QPushButton[size="small"] { min-height: 32px; border-radius: 16px; padding: 0 14px; }

QPushButton[accent="true"] {
    background: %(primary)s;
    border: 1px solid %(primary)s;
    color: %(primary_text)s;
    font-weight: 700;
    padding: 0 20px;
}
QPushButton[accent="true"]:hover { background: %(primary_hover)s; border-color: %(primary_hover)s; }
QPushButton[accent="true"]:disabled { background: %(border)s; border-color: %(border)s; color: %(muted)s; }

QPushButton[flat="true"] {
    background: transparent; border: none; color: %(text)s; padding: 0 8px; min-height: 32px;
}
QPushButton[flat="true"]:hover { color: %(heading)s; background: %(surface_alt)s; }
QPushButton[flat="true"]:disabled { color: %(muted)s; background: transparent; }

/* The footer's background-posting line: reads as text, acts as a toggle. */
QPushButton#statusLink {
    background: transparent; border: none; padding: 0; min-height: 22px;
    color: %(text)s; font-weight: 400; text-align: left;
}
QPushButton#statusLink:hover { color: %(heading)s; text-decoration: underline; }

QPushButton[danger="true"]:hover { border-color: %(danger)s; color: %(danger)s; }

QToolButton#iconButton {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: 18px;
    min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
}
QToolButton#iconButton:hover { border-color: %(primary)s; }
QToolButton#iconButton::menu-indicator { image: none; width: 0; }

QToolButton#accountChip {
    background: %(surface)s;
    border: 1px solid %(border)s;
    border-radius: 19px;
    padding: 3px 12px 3px 4px;
    min-height: 30px;
    color: %(heading)s;
    font-weight: 600;
}
QToolButton#accountChip:hover { border-color: %(primary)s; }
QToolButton#accountChip::menu-indicator { image: none; width: 0; }

/* Compose / Queue tabs. */
QFrame#segmented { background: %(seg)s; border-radius: 20px; }
QPushButton#tab {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 17px;
    min-height: 32px;
    padding: 0 18px;
    color: %(text)s;
    font-weight: 600;
}
QPushButton#tab:hover { color: %(heading)s; background: transparent; }
QPushButton#tab:checked {
    background: %(surface)s;
    border-color: %(input_border)s;
    color: %(heading)s;
}
QLabel#badge {
    background: %(primary)s;
    color: %(primary_text)s;
    border-radius: 10px;
    max-height: 20px; min-height: 20px;
    padding: 0;
    font-size: 12px;
    font-weight: 700;
}

QLabel[pill="pending"], QLabel[pill="posted"], QLabel[pill="failed"] {
    border-radius: 11px; padding: 0 10px; font-size: 12px; font-weight: 600;
    min-height: 22px; max-height: 22px;
}
QLabel[pill="pending"] { background: %(pending_bg)s; color: %(pending_fg)s; }
QLabel[pill="posted"] { background: %(done_bg)s; color: %(done_fg)s; }
QLabel[pill="failed"] { background: %(failed_bg)s; color: %(failed_fg)s; }

QFrame#queueRow { background: transparent; border: none; border-top: 1px solid %(border)s; }
QFrame#queueRow[first="true"] { border-top: none; }
QLabel#thumb { background: %(surface_alt)s; border-radius: 12px; color: %(muted)s; }

QPushButton#addTile {
    min-height: 140px; max-height: 140px; min-width: 196px; max-width: 196px;
    background: transparent;
    border: 1px dashed %(input_border)s;
    border-radius: 12px;
    color: %(text)s;
    font-weight: 600;
    padding: 0;
}
QPushButton#addTile:hover { border-color: %(primary)s; color: %(heading)s; background: transparent; }
QToolButton#tileRemove {
    background: %(overlay)s;
    border: none;
    border-radius: 13px;
    min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px;
}

QStatusBar { background: %(surface)s; border-top: 1px solid %(border)s; color: %(muted)s; }
QStatusBar::item { border: none; }

/* input_border, not border_strong: an input's outline is its only edge, and
   border_strong measured 1.3-2.2:1 against the grounds - under the 3:1 that
   WCAG asks of control boundaries. Focus uses primary for the same reason;
   the brand cyan is about 2:1 on white. */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateTimeEdit, QSpinBox {
    background: %(surface)s;
    border: 1px solid %(input_border)s;
    border-radius: 12px;
    padding: 5px 10px;
    color: %(heading)s;
    selection-background-color: %(primary)s;
    selection-color: %(primary_text)s;
}
QLineEdit { min-height: 24px; }
QLineEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QDateTimeEdit:hover {
    border-color: %(primary)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QDateTimeEdit:focus {
    border-color: %(primary)s;
}
QLineEdit:read-only { background: %(surface_alt)s; color: %(muted)s; }
QLineEdit:disabled, QPlainTextEdit:disabled { color: %(muted)s; background: %(bg)s; }

QComboBox, QDateTimeEdit { min-height: 22px; }
QComboBox#visibility { border-radius: 18px; }
QComboBox QAbstractItemView {
    background: %(surface)s;
    color: %(text)s;
    border: 1px solid %(input_border)s;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QComboBox QAbstractItemView::item {
    min-height: 28px;
    padding: 0 10px;
    border-radius: 4px;
}
QComboBox QAbstractItemView::item:hover { background: %(surface_alt)s; }
QComboBox QAbstractItemView::item:selected {
    background: %(primary)s;
    color: %(primary_text)s;
}

QTableWidget, QTableView {
    background: %(surface)s;
    alternate-background-color: %(surface_alt)s;
    border: 1px solid %(border)s;
    border-radius: 12px;
    gridline-color: %(border)s;
    selection-background-color: %(selection)s;
    selection-color: %(selection_text)s;
}
QHeaderView::section {
    background: %(surface_alt)s;
    color: %(muted)s;
    border: none;
    border-bottom: 1px solid %(border)s;
    padding: 6px;
    font-weight: 600;
}

QProgressBar {
    background: %(surface_alt)s;
    border: none;
    border-radius: 3px;
    max-height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk { background: %(accent)s; border-radius: 3px; }

QScrollBar:vertical, QScrollBar:horizontal {
    background: transparent; border: none; width: 10px; height: 10px; margin: 0;
}
QScrollBar::handle {
    background: %(border_strong)s; border-radius: 5px; min-height: 28px; min-width: 28px;
}
QScrollBar::handle:hover { background: %(muted)s; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* Styled in full because windows11 draws no box at all for an unchecked
   indicator under this stylesheet. Styling ::indicator also drops the native
   tick, hence the generated image. The border is `muted`, not a border token:
   it is the control's only outline, so it needs 3:1 against the ground. */
QCheckBox { background: transparent; spacing: 8px; }
QCheckBox::indicator {
    width: 16px; height: 16px;
    border: 1px solid %(muted)s;
    border-radius: 4px;
    background: %(surface)s;
}
QCheckBox::indicator:hover { border-color: %(primary)s; }
QCheckBox::indicator:checked {
    background: %(primary)s;
    border-color: %(primary)s;
    image: url("%(check_image)s");
}
QCheckBox::indicator:disabled { background: %(bg)s; border-color: %(border)s; }

QToolTip {
    background: %(surface)s; color: %(heading)s;
    border: 1px solid %(input_border)s; padding: 4px 6px;
}
""" % dict(t, check_image=t.get("check_image", "")) + (
        DROPDOWN_RULES % t
        if all(t.get(k) for k in ARROW_KEYS) else "")


# Every generated arrow the dropdown rules name. All or nothing: a styled
# sub-control whose image is missing draws no arrow at all.
ARROW_KEYS = ("chevron_image", "chevron_up_image", "chevron_left_image",
              "chevron_right_image")
ARROW_DIRECTIONS = (("down", "chevron_image"), ("up", "chevron_up_image"),
                    ("left", "chevron_left_image"), ("right", "chevron_right_image"))


# Styling ::drop-down replaces the whole sub-control and Qt then draws no arrow,
# so ::down-arrow must supply its own image. Only emitted when that image
# exists: a drop-down rule without one leaves the control looking inert.
DROPDOWN_RULES = """
QComboBox, QDateTimeEdit { padding: 0 30px 0 12px; min-height: 36px; }  /* = button height */
QComboBox::drop-down, QDateTimeEdit::drop-down {
    subcontrol-origin: padding;      /* inside the outline, which stays whole */
    subcontrol-position: top right;
    width: 30px;
    border: none;
    border-left: 1px solid %(input_border)s;
    border-top-right-radius: 11px;
    border-bottom-right-radius: 11px;
    background: %(surface_alt)s;
}
QComboBox#visibility::drop-down {
    border-left: none;
    background: transparent;
    border-top-right-radius: 17px;
    border-bottom-right-radius: 17px;
}
QComboBox::drop-down:hover, QDateTimeEdit::drop-down:hover,
QComboBox::drop-down:on { background: %(selection)s; }
QComboBox::down-arrow, QDateTimeEdit::down-arrow {
    image: url("%(chevron_image)s");
    width: 12px;
    height: 12px;
}

/* Number fields (the background-posting interval prompt): the same divided,
   tinted button column as a dropdown, split into up and down. */
QSpinBox { padding: 0 32px 0 12px; min-height: 36px; }
QSpinBox::up-button, QSpinBox::down-button {
    subcontrol-origin: padding;
    width: 30px;
    border: none;
    border-left: 1px solid %(input_border)s;
    background: %(surface_alt)s;
}
QSpinBox::up-button {
    subcontrol-position: top right;
    border-top-right-radius: 11px;
    border-bottom: 1px solid %(border)s;
}
QSpinBox::down-button {
    subcontrol-position: bottom right;
    border-bottom-right-radius: 11px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: %(selection)s; }
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed { background: %(border_strong)s; }
QSpinBox::up-arrow { image: url("%(chevron_up_image)s"); width: 10px; height: 10px; }
QSpinBox::down-arrow { image: url("%(chevron_image)s"); width: 10px; height: 10px; }
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off,
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off { image: none; }

/* The date field's calendar popup. */
QCalendarWidget QWidget#qt_calendar_navigationbar {
    background: %(surface_alt)s;
    border-bottom: 1px solid %(border)s;
}
QCalendarWidget QToolButton {
    background: transparent;
    color: %(text)s;
    border: none;
    border-radius: 4px;
    padding: 4px 8px;
    font-weight: 600;
}
QCalendarWidget QToolButton:hover { background: %(selection)s; }
QCalendarWidget QToolButton::menu-indicator { image: none; width: 0; }
QCalendarWidget QToolButton#qt_calendar_prevmonth {
    qproperty-icon: url("%(chevron_left_image)s");
    qproperty-iconSize: 14px;
}
QCalendarWidget QToolButton#qt_calendar_nextmonth {
    qproperty-icon: url("%(chevron_right_image)s");
    qproperty-iconSize: 14px;
}
QCalendarWidget QMenu { background: %(surface)s; }
/* The calendar's own year field: keep its compact size, narrower buttons. */
QCalendarWidget QSpinBox { min-height: 0; padding: 2px 20px 2px 4px; }
QCalendarWidget QSpinBox::up-button, QCalendarWidget QSpinBox::down-button { width: 18px; }
QCalendarWidget QAbstractItemView {
    background: %(surface)s;
    color: %(text)s;
    selection-background-color: %(primary)s;
    selection-color: %(primary_text)s;
    outline: none;
}
QCalendarWidget QAbstractItemView:disabled { color: %(muted)s; }
"""


# --------------------------------------------------------------------------
# icons: the same stroke paths the design mockups use, drawn by QtSvg
# --------------------------------------------------------------------------

ICON_PATHS = {
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.6 2.8 2.6 15.2 0 18"/><path d="M12 3c-2.6 2.8-2.6 15.2 0 18"/>',
    "unlisted": '<path d="M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6z"/><circle cx="12" cy="12" r="2.5"/><path d="M4 20L20 4"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18"/><path d="M8 3v4"/><path d="M16 3v4"/>',
    "sliders": '<path d="M4 6h9"/><path d="M17 6h3"/><circle cx="15" cy="6" r="2"/><path d="M4 12h3"/><path d="M11 12h9"/><circle cx="9" cy="12" r="2"/><path d="M4 18h11"/><path d="M19 18h1"/><circle cx="17" cy="18" r="2"/>',
    "plus": '<path d="M12 5v14"/><path d="M5 12h14"/>',
    "check": '<path d="M5 12l5 5L20 7"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "logout": '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M10 16l-4-4 4-4"/><path d="M6 12h10"/>',
    "login": '<path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><path d="M11 16l4-4-4-4"/><path d="M4 12h11"/>',
    "trash": '<path d="M4 7h16"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M9 7V4h6v3"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5"/><path d="M12 8h.01"/>',
    "x": '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
    "play": '<path d="M7 5l12 7-12 7z"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="3"/><circle cx="9" cy="9" r="1.5"/><path d="M21 15l-5-5L5 21"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "alert": '<path d="M12 3l9 16H3z"/><path d="M12 10v4"/><path d="M12 17h.01"/>',
}


def svg_pixmap(name: str, color: str, size: int = 18, stroke: float = 1.8, ratio: float = 2.0):
    """Render a named stroke icon to a crisp, device-pixel-ratio-aware pixmap."""
    from PySide6.QtCore import QByteArray, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    source = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
              'stroke="%s" stroke-width="%s" stroke-linecap="round" stroke-linejoin="round">%s</svg>'
              % (color, stroke, ICON_PATHS[name]))
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(source.encode("utf-8"))).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def svg_icon(name: str, color: str, size: int = 18, stroke: float = 1.8):
    from PySide6.QtGui import QIcon
    return QIcon(svg_pixmap(name, color, size, stroke))


def avatar_pixmap(text: str, size: int = 30, ratio: float = 2.0):
    """A cyan disc with the account's initial - pfpost cannot fetch the avatar
    on a write-only token, and a generic silhouette says less."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(LIGHT["accent"]))
    painter.drawEllipse(QRectF(0, 0, size * ratio, size * ratio))
    font = QFont(FONT_FAMILY)
    font.setBold(True)
    font.setPixelSize(int(size * ratio * 0.46))
    painter.setFont(font)
    painter.setPen(QColor("#08161f"))
    painter.drawText(QRectF(0, 0, size * ratio, size * ratio), Qt.AlignCenter,
                     (text or "?")[:1].upper())
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def rounded_pixmap(source, width: int, height: int, radius: float = 12, ratio: float = 2.0):
    """Crop-to-fill `source` into a rounded rectangle, as the mockups' thumbnails."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QPainter, QPainterPath, QPixmap

    w, h = int(width * ratio), int(height * ratio)
    scaled = source.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    x, y = (scaled.width() - w) // 2, (scaled.height() - h) // 2
    out = QPixmap(w, h)
    out.fill(Qt.transparent)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, w, h), radius * ratio, radius * ratio)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, scaled, x, y, w, h)
    painter.end()
    out.setDevicePixelRatio(ratio)
    return out


def check_image(color: str) -> str:
    """Write the checkbox tick as a PNG and return a stylesheet-ready path.

    Qt stylesheets can only load images from files or compiled resources - not
    data URIs - so it is drawn once into the temp directory. PNG rather than
    SVG: SVG needs an image plugin a frozen build could lack, and a missing
    tick would make a ticked box look unticked.
    """
    return _stroke_image("check", color, ((0.22, 0.52), (0.42, 0.72), (0.78, 0.30)))


CHEVRONS = {
    "down": ((0.20, 0.36), (0.50, 0.66), (0.80, 0.36)),
    "up": ((0.20, 0.64), (0.50, 0.34), (0.80, 0.64)),
    "left": ((0.62, 0.20), (0.34, 0.50), (0.62, 0.80)),
    "right": ((0.38, 0.20), (0.66, 0.50), (0.38, 0.80)),
}


def chevron_image(color: str, direction: str = "down") -> str:
    """A chevron arrow. Styling ::drop-down replaces Qt's own arrow, and the
    calendar's month buttons draw a black triangle whatever the theme, so the
    stylesheet has to supply these or the controls look inert."""
    name = "chevron" if direction == "down" else "chevron-%s" % direction
    return _stroke_image(name, color, CHEVRONS[direction])


def _stroke_image(kind: str, color: str, points) -> str:
    """Draw a round-capped polyline into a cached PNG; return its path."""
    import tempfile
    from pathlib import Path
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    path = Path(tempfile.gettempdir()) / ("pfpost-%s-%s.png" % (kind, color.lstrip("#")))
    if not path.exists():
        size = 64                   # drawn large; Qt scales it down to the control
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(size * 0.14)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        (x, y), rest = points[0], points[1:]
        line = QPainterPath(QPointF(size * x, size * y))
        for x, y in rest:
            line.lineTo(QPointF(size * x, size * y))
        painter.drawPath(line)
        painter.end()
        image.save(str(path), "PNG")
    return path.as_posix()


FONT_FAMILY = "IBM Plex Sans"
FONT_FILE = "IBMPlexSans[wdth,wght].ttf"
FONT_POINT_SIZE = 10


def install_font(app) -> str | None:
    """Make IBM Plex Sans the application font; return the family, or None.

    Plex is what Pixelfed's signed-in web app uses (its spa.css). It is bundled
    under the SIL Open Font License - see pfpost/fonts/OFL.txt - because
    Windows does not ship it. If it cannot load, the platform font stays.
    """
    from pathlib import Path
    from PySide6.QtGui import QFont, QFontDatabase

    path = Path(__file__).resolve().parent / "fonts" / FONT_FILE
    if not path.is_file():
        return None
    font_id = QFontDatabase.addApplicationFont(str(path))
    if font_id < 0 or FONT_FAMILY not in QFontDatabase.applicationFontFamilies(font_id):
        return None
    font = QFont()
    font.setFamilies([FONT_FAMILY, "Segoe UI"])
    font.setPointSize(FONT_POINT_SIZE)
    app.setFont(font)
    return FONT_FAMILY


def is_dark(app) -> bool:
    """Follow the desktop's light/dark setting.

    Qt 6.5+ reports it directly; older builds get a luminance comparison of the
    palette Qt actually handed us, which is right often enough and never throws.
    """
    try:
        from PySide6.QtCore import Qt as _Qt
        scheme = app.styleHints().colorScheme()
        if scheme == _Qt.ColorScheme.Dark:
            return True
        if scheme == _Qt.ColorScheme.Light:
            return False
    except Exception:
        pass
    try:
        window = app.palette().window().color()
        return window.lightness() < 128
    except Exception:
        return False


# The app icon keeps the blue it shipped with; the UI palette moved on.
ICON_BLUE = "#2c78bf"

# Every size Windows asks an .exe for, across Explorer's views and DPI scales.
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def draw_icon(size: int):
    """Draw the app icon at one size: a gradient tile in the brand colours.

    Generated rather than shipped, so there is no binary asset to keep in sync -
    and deliberately not a copy of Pixelfed's own mark. Each size is drawn
    natively rather than shrunk from 256px, and the strokes have a floor in
    whole pixels: at 16px a proportional ring is 1.2px wide and antialiasing
    smears it into a grey smudge.

    A QImage, not a QPixmap, so build.py can call it with no QApplication.
    """
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QImage, QLinearGradient, QPainter, QPen

    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    gradient = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    gradient.setColorAt(0.0, QColor(LIGHT["accent"]))
    gradient.setColorAt(1.0, QColor(ICON_BLUE))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)

    white = QColor(255, 255, 255, 235)
    stroke = max(size * 0.075, 2.0)
    pen = QPen(white)
    pen.setWidthF(stroke)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    inset = size * (0.27 if size <= 24 else 0.30)
    painter.drawEllipse(QRectF(inset, inset, size - inset * 2, size - inset * 2))

    painter.setBrush(white)
    painter.setPen(Qt.NoPen)
    dot = max(size * 0.085, 2.5)
    painter.drawEllipse(QRectF(size * 0.70, size * 0.19, dot, dot))
    painter.end()
    return image


def make_icon(size: int = 256):
    """The app icon as a QIcon carrying every size, so the title bar and
    taskbar pick a natively drawn one instead of scaling a large one down."""
    from PySide6.QtGui import QIcon, QPixmap

    icon = QIcon()
    for each in sorted(set(ICON_SIZES) | {size}):
        icon.addPixmap(QPixmap.fromImage(draw_icon(each)))
    return icon


def write_ico(path) -> None:
    """Write the icon as a multi-size Windows .ico, for the executables.

    The ICO container is a directory of entries, each here holding a PNG,
    which Windows has read since Vista. Written by hand because Qt's ICO
    writer stores a single size, and Pillow would be a build dependency
    for thirty lines of struct packing.
    """
    import struct
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    blobs = []
    for size in ICON_SIZES:
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.WriteOnly)
        draw_icon(size).save(buffer, "PNG")
        buffer.close()
        blobs.append((size, bytes(data)))

    header = struct.pack("<HHH", 0, 1, len(blobs))          # reserved, type=icon
    offset = len(header) + 16 * len(blobs)
    entries, body = b"", b""
    for size, png in blobs:
        dim = 0 if size >= 256 else size                     # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                               len(png), offset + len(body))
        body += png
    with open(path, "wb") as handle:
        handle.write(header + entries + body)
