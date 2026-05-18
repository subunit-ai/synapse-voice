"""v0.10.0 Hub sidebar — narrow column on the left holding the 6
section nav buttons. Active button is highlighted; click emits the
section key so the Hub can swap its content pane."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QPushButton, QSizePolicy, QVBoxLayout, QWidget


SIDEBAR_BG = "#080f1d"
ACTIVE_BG = "#06b6d4"
ACTIVE_FG = "#031426"
INACTIVE_FG = "#94a3b8"
HOVER_BG = "#1e293b"

# Section keys + labels + glyphs. Glyphs are unicode so we don't need
# to ship icon font assets — keeps the bundle slim.
SECTIONS: list[tuple[str, str, str]] = [
    ("home",       "Home",       "⌂"),
    ("history",    "Verlauf",    "⏱"),
    ("meetings",   "Meetings",   "🎙"),
    ("vocabulary", "Vocabulary", "📖"),
    ("settings",   "Settings",   "⚙"),
    ("help",       "Hilfe",      "ⓘ"),
]


class _NavButton(QPushButton):
    def __init__(self, key: str, label: str, glyph: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(56)
        self.setText(f" {glyph}   {label}")
        f = QFont()
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.Medium)
        self.setFont(f)
        # Stylesheet: idle = transparent + muted text, hover = surface tint,
        # checked = brand cyan with dark text. Border-left makes the active
        # state read at a glance even when the user is on a sub-tab.
        self.setStyleSheet(
            f"""
            QPushButton {{
                background: transparent;
                color: {INACTIVE_FG};
                border: none;
                border-left: 3px solid transparent;
                padding: 0 16px;
                text-align: left;
            }}
            QPushButton:hover {{
                background: {HOVER_BG};
                color: #e2e8f0;
            }}
            QPushButton:checked {{
                background: {SIDEBAR_BG};
                color: {ACTIVE_BG};
                border-left: 3px solid {ACTIVE_BG};
                font-weight: 700;
            }}
            """
        )


class HubSidebar(QWidget):
    """200px wide nav column."""

    section_changed = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("hubSidebar")
        self.setFixedWidth(200)
        self.setStyleSheet(
            f"QWidget#hubSidebar {{ background: {SIDEBAR_BG}; border-right: 1px solid #111c30; }}"
        )

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 12, 0, 12)
        col.setSpacing(2)

        self._buttons: dict[str, _NavButton] = {}
        for key, label, glyph in SECTIONS:
            btn = _NavButton(key, label, glyph, self)
            btn.clicked.connect(lambda _checked=False, k=key: self.select(k))
            self._buttons[key] = btn
            col.addWidget(btn)

        col.addStretch(1)

        # Default selection — Home is checked from the start so the visual
        # never flashes blank.
        self._buttons["home"].setChecked(True)

    def select(self, key: str) -> None:
        if key not in self._buttons:
            return
        for k, btn in self._buttons.items():
            btn.setChecked(k == key)
        self.section_changed.emit(key)

    def current(self) -> str:
        for k, btn in self._buttons.items():
            if btn.isChecked():
                return k
        return "home"
