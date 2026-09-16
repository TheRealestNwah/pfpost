"""Visual theme, built from Pixelfed's own palette.

Colours were taken from a live instance rather than guessed:

    --primary    #2c78bf   links, buttons
    theme-color  #10c5f8   the brand cyan
    --dark       #212529   --light  #f8f9fa   --gray-dark  #343a40
    danger #dc3545   success #28a745   warning #ffc107   info #17a2b8

Pixelfed ships Bootstrap, so the neutrals are Bootstrap's grey scale. The dark
variant is derived from the same hues rather than a separate palette, so the two
read as one design.

Everything here is data plus one string builder; no Qt widget logic, so the
tokens can be asserted in tests without a window.
"""

from __future__ import annotations

LIGHT = {
    "name": "light",
    "accent": "#10c5f8",        # brand cyan - highlights, focus, the icon
    "primary": "#2c78bf",       # Pixelfed's link/button blue
    "primary_hover": "#245f98",
    "primary_text": "#ffffff",
    "bg": "#f8f9fa",
    "surface": "#ffffff",
    "surface_alt": "#f1f3f5",
    "border": "#dee2e6",
    "border_strong": "#ced4da",
    "input_border": "#80868c",  # >= 3.3:1 on every light ground
    "text": "#212529",
    "muted": "#6c757d",
    "danger": "#dc3545",
    "success": "#28a745",
    "warning": "#b8860b",       # #ffc107 is unreadable on light; darkened
    "selection": "#d5e6f5",
    "selection_text": "#12263a",
}

DARK = {
    "name": "dark",
    "accent": "#10c5f8",
    "primary": "#4da3e8",       # #2c78bf is too dim on a dark ground
    "primary_hover": "#6cb6ef",
    "primary_text": "#08161f",
    "bg": "#16191d",
    "surface": "#1d2126",
    "surface_alt": "#23282e",
    "border": "#343a40",
    "border_strong": "#495057",
    "input_border": "#737b83",  # >= 3.4:1 on every dark ground
    "text": "#e9ecef",
    "muted": "#9aa4ae",
    "danger": "#f1707b",        # lightened so it stays legible on dark
    "success": "#4fc76d",
    "warning": "#ffc107",
    "selection": "#1e3f5a",
    "selection_text": "#e9ecef",
}


def tokens(dark: bool) -> dict:
    return dict(DARK if dark else LIGHT)


def stylesheet(t: dict) -> str:
    """Qt stylesheet for the whole application."""
    return """
QWidget {
    background: %(bg)s;
    color: %(text)s;
}
QMainWindow, QDialog { background: %(bg)s; }

QLabel { background: transparent; }
QLabel[role="muted"] { color: %(muted)s; }
QLabel[role="heading"] { font-weight: 600; font-size: 15px; }

QMenuBar { background: %(surface)s; border-bottom: 1px solid %(border)s; }
QMenuBar::item { padding: 6px 10px; background: transparent; }
QMenuBar::item:selected { background: %(surface_alt)s; color: %(primary)s; }
QMenu { background: %(surface)s; border: 1px solid %(border)s; padding: 4px; }
QMenu::item { padding: 6px 22px 6px 12px; border-radius: 4px; }
QMenu::item:selected { background: %(primary)s; color: %(primary_text)s; }
QMenu::separator { height: 1px; background: %(border)s; margin: 4px 6px; }

QStatusBar { background: %(surface)s; border-top: 1px solid %(border)s; color: %(muted)s; }
QStatusBar::item { border: none; }

QPushButton {
    background: %(surface)s;
    border: 1px solid %(border_strong)s;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 20px;
}
QPushButton:hover { background: %(surface_alt)s; border-color: %(primary)s; }
QPushButton:pressed { background: %(selection)s; }
QPushButton:disabled { color: %(muted)s; border-color: %(border)s; background: %(bg)s; }
QPushButton:focus { outline: none; border-color: %(accent)s; }

QPushButton[accent="true"] {
    background: %(primary)s;
    border: 1px solid %(primary)s;
    color: %(primary_text)s;
    font-weight: 600;
}
QPushButton[accent="true"]:hover { background: %(primary_hover)s; border-color: %(primary_hover)s; }
QPushButton[accent="true"]:disabled { background: %(border)s; border-color: %(border)s; color: %(muted)s; }

QPushButton[danger="true"]:hover { border-color: %(danger)s; color: %(danger)s; }

/* input_border, not border_strong: an input's outline is its only edge, and
   border_strong measured 1.3-2.2:1 against the grounds - under the 3:1 that
   WCAG asks of control boundaries. Focus uses primary for the same reason;
   the brand cyan is about 2:1 on white. */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateTimeEdit, QSpinBox {
    background: %(surface)s;
    border: 1px solid %(input_border)s;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: %(primary)s;
    selection-color: %(primary_text)s;
}
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
    border-radius: 6px;
    gridline-color: %(border)s;
    selection-background-color: %(selection)s;
    selection-color: %(selection_text)s;
}
QTableWidget::item { padding: 4px 6px; }
QHeaderView::section {
    background: %(surface_alt)s;
    color: %(muted)s;
    border: none;
    border-bottom: 1px solid %(border)s;
    padding: 6px;
    font-weight: 600;
}
QTableCornerButton::section { background: %(surface_alt)s; border: none; }

QProgressBar {
    background: %(surface_alt)s;
    border: none;
    border-radius: 3px;
    height: 6px;
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

QSplitter::handle { background: %(border)s; height: 1px; }
QToolTip {
    background: %(surface)s; color: %(text)s;
    border: 1px solid %(border_strong)s; padding: 4px 6px;
}

#accountBar { background: %(surface)s; border-bottom: 1px solid %(border)s; }
#sectionLabel { color: %(muted)s; font-weight: 600; }
#dropHint { color: %(muted)s; }
""" % dict(t, check_image=t.get("check_image", "")) + (
        DROPDOWN_RULES % t
        if all(t.get(k) for k in ("chevron_image", "chevron_left_image",
                                  "chevron_right_image")) else "")


# Styling ::drop-down replaces the whole sub-control and Qt then draws no arrow,
# so ::down-arrow must supply its own image. Only emitted when that image
# exists: a drop-down rule without one leaves the control looking inert.
DROPDOWN_RULES = """
QComboBox, QDateTimeEdit { padding: 0 30px 0 10px; min-height: 32px; }  /* = button height */
QComboBox::drop-down, QDateTimeEdit::drop-down {
    subcontrol-origin: padding;      /* inside the outline, which stays whole */
    subcontrol-position: top right;
    width: 30px;
    border: none;
    border-left: 1px solid %(input_border)s;
    border-top-right-radius: 5px;
    border-bottom-right-radius: 5px;
    background: %(surface_alt)s;
}
QComboBox::drop-down:hover, QDateTimeEdit::drop-down:hover,
QComboBox::drop-down:on { background: %(selection)s; }
QComboBox::down-arrow, QDateTimeEdit::down-arrow {
    image: url("%(chevron_image)s");
    width: 12px;
    height: 12px;
}

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
QCalendarWidget QSpinBox { min-height: 0; padding: 2px 4px; }
QCalendarWidget QAbstractItemView {
    background: %(surface)s;
    color: %(text)s;
    selection-background-color: %(primary)s;
    selection-color: %(primary_text)s;
    outline: none;
}
QCalendarWidget QAbstractItemView:disabled { color: %(muted)s; }
"""


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
    gradient.setColorAt(1.0, QColor(LIGHT["primary"]))
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
