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

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QDateTimeEdit, QSpinBox {
    background: %(surface)s;
    border: 1px solid %(border_strong)s;
    border-radius: 6px;
    padding: 5px 8px;
    selection-background-color: %(primary)s;
    selection-color: %(primary_text)s;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QDateTimeEdit:focus {
    border-color: %(accent)s;
}
QLineEdit:read-only { background: %(surface_alt)s; color: %(muted)s; }
QLineEdit:disabled, QPlainTextEdit:disabled { color: %(muted)s; background: %(bg)s; }

/* Deliberately not styling ::drop-down. Doing so replaces the whole
   sub-control, and Qt then draws no arrow at all, leaving both the
   visibility picker and the date field looking like inert boxes. */
QComboBox, QDateTimeEdit { padding-right: 4px; }
QComboBox QAbstractItemView {
    background: %(surface)s;
    border: 1px solid %(border)s;
    selection-background-color: %(primary)s;
    selection-color: %(primary_text)s;
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
""" % dict(t, check_image=t.get("check_image", ""))


def check_image(color: str) -> str:
    """Write the checkbox tick as a PNG and return a stylesheet-ready path.

    Qt stylesheets can only load images from files or compiled resources - not
    data URIs - so it is drawn once into the temp directory. PNG rather than
    SVG: SVG needs an image plugin a frozen build could lack, and a missing
    tick would make a ticked box look unticked.
    """
    import tempfile
    from pathlib import Path
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

    path = Path(tempfile.gettempdir()) / ("pfpost-check-%s.png" % color.lstrip("#"))
    if not path.exists():
        size = 64                   # drawn large; Qt scales it to the 16px box
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(size * 0.14)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        tick = QPainterPath(QPointF(size * 0.22, size * 0.52))
        tick.lineTo(QPointF(size * 0.42, size * 0.72))
        tick.lineTo(QPointF(size * 0.78, size * 0.30))
        painter.drawPath(tick)
        painter.end()
        image.save(str(path), "PNG")
    return path.as_posix()


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
