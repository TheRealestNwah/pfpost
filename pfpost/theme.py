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

QComboBox::drop-down, QDateTimeEdit::drop-down {
    border: none; width: 20px;
}
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

QSplitter::handle { background: %(border)s; height: 1px; }
QToolTip {
    background: %(surface)s; color: %(text)s;
    border: 1px solid %(border_strong)s; padding: 4px 6px;
}

#accountBar { background: %(surface)s; border-bottom: 1px solid %(border)s; }
#sectionLabel { color: %(muted)s; font-weight: 600; }
#dropHint { color: %(muted)s; }
""" % t


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


def make_icon(size: int = 256):
    """Draw the app icon: a gradient tile in the brand colours.

    Generated rather than shipped, so there is no binary asset to keep in sync -
    and deliberately not a copy of Pixelfed's own mark.
    """
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QIcon, QLinearGradient, QPainter, QPen, QPixmap

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    gradient = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    gradient.setColorAt(0.0, QColor(LIGHT["accent"]))
    gradient.setColorAt(1.0, QColor(LIGHT["primary"]))
    painter.setBrush(QBrush(gradient))
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)

    pen = QPen(QColor(255, 255, 255, 235))
    pen.setWidthF(size * 0.075)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    inset = size * 0.30
    painter.drawEllipse(QRectF(inset, inset, size - inset * 2, size - inset * 2))

    painter.setBrush(QColor(255, 255, 255, 235))
    painter.setPen(Qt.NoPen)
    dot = size * 0.085
    painter.drawEllipse(QRectF(size * 0.70, size * 0.19, dot, dot))
    painter.end()

    return QIcon(pixmap)
