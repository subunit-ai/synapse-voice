"""Searchable language picker popup — invoked from the Orb's left
satellite button. 99 Whisper-supported codes, fuzzy-filterable by
typing.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..languages import LANGUAGES, display_name

CYAN = "#40d6ff"
NIGHT = "#020817"
NIGHT_2 = "#0c1828"
NIGHT_BORDER = "#1f3145"
WHITE = "#e6f2fb"
WHITE_DIM = "#9fb1bd"

QSS = f"""
QWidget#langPopup {{ background: {NIGHT}; border: 1px solid {NIGHT_BORDER}; border-radius: 14px; }}
QLabel#title {{ color: {WHITE_DIM}; font-size: 11px; font-weight: 600; letter-spacing: 1.5px; }}
QLineEdit {{ background: {NIGHT_2}; color: {WHITE}; border: 1px solid {NIGHT_BORDER}; border-radius: 8px; padding: 8px 10px; }}
QLineEdit:focus {{ border-color: {CYAN}; }}
QListWidget {{ background: {NIGHT_2}; color: {WHITE}; border: 1px solid {NIGHT_BORDER}; border-radius: 8px; padding: 4px; outline: 0; }}
QListWidget::item {{ padding: 7px 10px; border-radius: 6px; }}
QListWidget::item:selected {{ background: #143246; color: {WHITE}; }}
QListWidget::item:hover {{ background: #0f2638; }}
"""


class LangPickerPopup(QWidget):
    """Compact popup, dismissed on Escape / click-outside / pick. Persists
    the user's choice via a callback rather than mutating Config directly
    so the orb can react immediately."""

    def __init__(self, current: str, on_pick: Callable[[str], None]) -> None:
        super().__init__()
        self._on_pick = on_pick
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Popup
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("langPopup")
        self.resize(320, 380)
        self.setStyleSheet(QSS)

        wrapper = QFrame()
        wrapper.setObjectName("langPopup")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(wrapper)
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QLabel(f"LANGUAGE  ·  {len(LANGUAGES)} options")
        title.setObjectName("title")
        layout.addWidget(title)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search language…")
        self.search.textChanged.connect(self._refilter)
        layout.addWidget(self.search)

        self.list = QListWidget()
        self.list.itemActivated.connect(self._on_pick_item)
        self.list.itemClicked.connect(self._on_pick_item)
        layout.addWidget(self.list, 1)

        self._populate(current)
        # Auto-focus the search box so typing starts filtering immediately.
        self.search.setFocus()

    def _populate(self, current: str) -> None:
        self.list.clear()
        for code, name in LANGUAGES:
            it = QListWidgetItem(f"{name}    ·    {code}")
            it.setData(Qt.ItemDataRole.UserRole, code)
            if code == (current or "").lower():
                it.setForeground(QColor(CYAN))
            self.list.addItem(it)
        # Pre-select the current language so Enter applies it immediately.
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole) == current:
                self.list.setCurrentRow(i)
                break

    def _refilter(self, q: str) -> None:
        q = q.strip().lower()
        if not q:
            for i in range(self.list.count()):
                self.list.item(i).setHidden(False)
            return
        for i in range(self.list.count()):
            it = self.list.item(i)
            label = it.text().lower()
            code = (it.data(Qt.ItemDataRole.UserRole) or "").lower()
            it.setHidden(q not in label and q not in code)
        # Auto-jump to the first visible row so Enter works on the top match.
        for i in range(self.list.count()):
            if not self.list.item(i).isHidden():
                self.list.setCurrentRow(i)
                break

    def _on_pick_item(self, item: QListWidgetItem) -> None:
        code = item.data(Qt.ItemDataRole.UserRole)
        if code:
            self._on_pick(code)
        self.close()

    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            cur = self.list.currentItem()
            if cur and not cur.isHidden():
                self._on_pick_item(cur)
                return
        super().keyPressEvent(e)
