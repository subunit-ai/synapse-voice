"""App-wide theme system.

Two palettes — Dark (Subunit brand default, deep navy + cyan) and Light
(neutral grey + cyan). Applied globally via QApplication.setPalette() +
a thin QSS stylesheet for chrome that QPalette can't reach (tooltips,
group boxes, list items).

Usage:
    from synapse_voice import theme
    theme.apply(QApplication.instance(), config.ui_theme)
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette


# Subunit brand accent — same on both themes so the orb / hover states
# stay recognisable across modes.
ACCENT = "#06b6d4"
ACCENT_DIM = "#0891b2"


def apply(app, theme: str) -> None:
    """Apply `theme` ("dark" | "light") to the running QApplication."""
    if theme == "light":
        app.setPalette(_light_palette())
        app.setStyleSheet(_LIGHT_QSS)
    else:
        app.setPalette(_dark_palette())
        app.setStyleSheet(_DARK_QSS)


def _dark_palette() -> QPalette:
    p = QPalette()
    bg = QColor("#0f172a")  # deep navy
    surface = QColor("#1e293b")
    text = QColor("#e2e8f0")
    muted = QColor("#94a3b8")
    accent = QColor(ACCENT)

    p.setColor(QPalette.ColorRole.Window, bg)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, surface)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#293548"))
    p.setColor(QPalette.ColorRole.ToolTipBase, surface)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorRole.Button, surface)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor("#f87171"))
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#0f172a"))
    p.setColor(QPalette.ColorRole.Link, accent)
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(ACCENT_DIM))
    return p


def _light_palette() -> QPalette:
    p = QPalette()
    bg = QColor("#f8fafc")
    surface = QColor("#ffffff")
    text = QColor("#0f172a")
    muted = QColor("#64748b")
    accent = QColor(ACCENT)

    p.setColor(QPalette.ColorRole.Window, bg)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, surface)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor("#f1f5f9"))
    p.setColor(QPalette.ColorRole.ToolTipBase, surface)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorRole.Button, surface)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.BrightText, QColor("#dc2626"))
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.ColorRole.Link, accent)
    p.setColor(QPalette.ColorRole.LinkVisited, QColor(ACCENT_DIM))
    return p


# Tiny stylesheet — QPalette covers most things; QSS handles tooltips,
# scrollbars and a few QGroupBox / QLineEdit quirks per theme. Widgets
# that paint custom (Orb, BrandLogo, KeyVisualizer) read brand colours
# directly and ignore the palette.
_DARK_QSS = """
QToolTip {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    padding: 4px 8px;
    border-radius: 4px;
}
QGroupBox {
    border: 1px solid #334155;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #94a3b8;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: #06b6d4;
    selection-color: #0f172a;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #06b6d4;
}
QPushButton {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover { background-color: #293548; border-color: #475569; }
QPushButton:pressed { background-color: #0f172a; }
QPushButton:disabled { color: #475569; }
"""

_LIGHT_QSS = """
QToolTip {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    padding: 4px 8px;
    border-radius: 4px;
}
QGroupBox {
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #64748b;
}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: #06b6d4;
    selection-color: #ffffff;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #06b6d4;
}
QPushButton {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px 14px;
}
QPushButton:hover { background-color: #f1f5f9; border-color: #94a3b8; }
QPushButton:pressed { background-color: #e2e8f0; }
QPushButton:disabled { color: #cbd5e1; }
"""
