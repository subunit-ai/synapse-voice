"""Click-to-record hotkey input — captures the next keypress as a hotkey combo.

Output format matches pynput.GlobalHotKeys (e.g. `<ctrl>+<shift>+<space>`).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QPushButton

# Qt key → pynput-compatible token
_SPECIAL_KEY_MAP = {
    Qt.Key.Key_Space: "<space>",
    Qt.Key.Key_Tab: "<tab>",
    Qt.Key.Key_Return: "<enter>",
    Qt.Key.Key_Enter: "<enter>",
    Qt.Key.Key_Escape: "<esc>",
    Qt.Key.Key_Backspace: "<backspace>",
    Qt.Key.Key_Delete: "<delete>",
    Qt.Key.Key_Home: "<home>",
    Qt.Key.Key_End: "<end>",
    Qt.Key.Key_PageUp: "<page_up>",
    Qt.Key.Key_PageDown: "<page_down>",
    Qt.Key.Key_Up: "<up>",
    Qt.Key.Key_Down: "<down>",
    Qt.Key.Key_Left: "<left>",
    Qt.Key.Key_Right: "<right>",
    Qt.Key.Key_F1: "<f1>",
    Qt.Key.Key_F2: "<f2>",
    Qt.Key.Key_F3: "<f3>",
    Qt.Key.Key_F4: "<f4>",
    Qt.Key.Key_F5: "<f5>",
    Qt.Key.Key_F6: "<f6>",
    Qt.Key.Key_F7: "<f7>",
    Qt.Key.Key_F8: "<f8>",
    Qt.Key.Key_F9: "<f9>",
    Qt.Key.Key_F10: "<f10>",
    Qt.Key.Key_F11: "<f11>",
    Qt.Key.Key_F12: "<f12>",
}


class HotkeyCaptureButton(QPushButton):
    """Push the button → it listens for the next combo and emits captured."""

    captured = pyqtSignal(str)

    def __init__(self, current: str = "", parent=None) -> None:
        super().__init__(parent)
        self._current = current
        self._listening = False
        self.clicked.connect(self._start_listen)
        self._render()

    def value(self) -> str:
        return self._current

    def setValue(self, combo: str) -> None:
        self._current = combo
        self._render()

    def _start_listen(self) -> None:
        self._listening = True
        self.setText("Press combo… (Esc to cancel)")
        self.setFocus()

    def _render(self) -> None:
        self.setText(self._current or "<click to set>")

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._listening:
            super().keyPressEvent(event)
            return

        key = event.key()

        # Cancel on Escape
        if key == Qt.Key.Key_Escape:
            self._listening = False
            self._render()
            return

        # Ignore lone modifier presses — wait for actual key
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            return

        modifiers = event.modifiers()
        parts: list[str] = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("<ctrl>")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("<shift>")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("<alt>")
        if modifiers & Qt.KeyboardModifier.MetaModifier:
            parts.append("<cmd>")

        token = _SPECIAL_KEY_MAP.get(key)
        if token is None:
            text = event.text().lower()
            if text and text.isprintable() and not text.isspace():
                token = text
            else:
                # unsupported lone key — keep listening
                return

        parts.append(token)
        combo = "+".join(parts)
        self._current = combo
        self._listening = False
        self._render()
        self.captured.emit(combo)
