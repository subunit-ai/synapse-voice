"""Click-to-record hotkey input — captures the next keypress as a hotkey combo.

Output format matches pynput.GlobalHotKeys (e.g. `<ctrl>+<shift>+<space>`).
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QPushButton


# 2026-05-14 (codex polish #1): warn when the chosen shortcut is likely
# captured by the OS or by very common applications. Sonar uses
# pynput.GlobalHotKeys which will *try* to register, but in practice
# the OS/browser/IDE will still grab the keystroke first — the user
# sees a hotkey that "doesn't work" with no obvious reason. A clear
# warning before they finalize the setting saves a support ticket.

# Map: hotkey string → human-readable conflict reason. Combos are
# normalized to lowercase before lookup. Single-key entries (no <ctrl>
# etc) are handled separately.
_KNOWN_CONFLICTS: dict[str, str] = {
    "<ctrl>+c": "system copy",
    "<ctrl>+v": "system paste — would break dictation paste-back",
    "<ctrl>+x": "system cut",
    "<ctrl>+a": "select-all in most apps",
    "<ctrl>+z": "undo",
    "<ctrl>+y": "redo (Windows)",
    "<ctrl>+s": "save in most apps",
    "<ctrl>+f": "find in most apps",
    "<ctrl>+t": "new tab in browsers",
    "<ctrl>+w": "close tab in browsers",
    "<ctrl>+n": "new window in many apps",
    "<ctrl>+p": "print",
    "<ctrl>+r": "reload in browsers",
    "<ctrl>+l": "address bar / Lock-screen on Windows",
    "<ctrl>+q": "quit on Linux/Windows",
    "<ctrl>+<tab>": "browser tab cycle",
    "<ctrl>+<shift>+<tab>": "browser tab cycle (reverse)",
    "<alt>+<tab>": "OS window switcher",
    "<alt>+<f4>": "close app (Windows/Linux)",
    "<cmd>+<space>": "Spotlight (macOS)",
    "<cmd>+q": "quit (macOS)",
    "<cmd>+w": "close window (macOS)",
    "<cmd>+<tab>": "OS app switcher (macOS)",
    "<ctrl>+<alt>+<delete>": "Windows secure attention",
    "<ctrl>+<alt>+t": "open terminal (GNOME)",
    "<ctrl>+<shift>+t": "reopen closed tab in browsers",
    "<ctrl>+<shift>+n": "new private window in browsers",
}


def detect_hotkey_conflict(combo: str) -> str | None:
    """Return a human-readable reason if ``combo`` is likely to conflict.

    Returns ``None`` when the hotkey looks safe. The check is best-effort
    — we cannot enumerate every OS/app shortcut, but we cover the very
    common cases where users would otherwise blame Sonar for not working.
    """
    if not combo:
        return "no hotkey set"

    normalized = combo.strip().lower()
    parts = [p for p in normalized.split("+") if p]
    if not parts:
        return "no hotkey set"

    # Lone modifier-only combos can't fire (pynput will never trigger).
    modifiers = {"<ctrl>", "<shift>", "<alt>", "<cmd>", "<meta>"}
    non_modifier_parts = [p for p in parts if p not in modifiers]
    if not non_modifier_parts:
        return "only modifier keys — Sonar will never trigger"

    # Lone alphanumeric (no modifier) → conflicts with typing.
    if len(parts) == 1 and parts[0] not in modifiers:
        token = parts[0]
        # Function keys + <esc>/<space>/<enter>/etc are acceptable solo.
        safe_solo = {f"<f{i}>" for i in range(1, 13)} | {
            "<esc>", "<pause>", "<scroll_lock>", "<insert>",
            "<print_screen>",
        }
        if token in safe_solo:
            return None
        return f"lone '{token}' will fire while typing"

    # Exact match against the known-conflict map.
    if normalized in _KNOWN_CONFLICTS:
        return _KNOWN_CONFLICTS[normalized]

    return None

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
