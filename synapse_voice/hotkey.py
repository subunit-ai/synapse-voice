"""Global hotkey listener via pynput, marshalled to Qt signals.

Two modes:
  - "toggle": single press toggles record on/off (default, behaviour from
    earlier versions).
  - "hold":   hold the combo to record, release to stop. Wispr-Flow style.

Hold-mode has its own listener implementation because pynput's
GlobalHotKeys only fires on the *press* edge — we need to hook all key
events to detect the release.
"""
from __future__ import annotations

import threading
from typing import Callable, Iterable, Optional

from pynput import keyboard


def _parse_combo(combo: str) -> set[str]:
    """Parse '<ctrl>+<shift>+<space>' into a set of canonical tokens."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    out: set[str] = set()
    for p in parts:
        out.add(p.strip("<>"))
    return out


class _HoldHotkey:
    """Press-to-record / release-to-stop variant."""

    # Map pynput keys to canonical lowercase tokens.
    _MOD_MAP = {
        keyboard.Key.ctrl: "ctrl",
        keyboard.Key.ctrl_l: "ctrl",
        keyboard.Key.ctrl_r: "ctrl",
        keyboard.Key.shift: "shift",
        keyboard.Key.shift_l: "shift",
        keyboard.Key.shift_r: "shift",
        keyboard.Key.alt: "alt",
        keyboard.Key.alt_l: "alt",
        keyboard.Key.alt_r: "alt",
        keyboard.Key.alt_gr: "alt",
        keyboard.Key.cmd: "cmd",
        keyboard.Key.cmd_l: "cmd",
        keyboard.Key.cmd_r: "cmd",
    }
    _NAMED = {
        keyboard.Key.space: "space",
        keyboard.Key.enter: "enter",
        keyboard.Key.tab: "tab",
        keyboard.Key.esc: "esc",
        keyboard.Key.f1: "f1",
        keyboard.Key.f2: "f2",
        keyboard.Key.f3: "f3",
        keyboard.Key.f4: "f4",
        keyboard.Key.f5: "f5",
        keyboard.Key.f6: "f6",
        keyboard.Key.f7: "f7",
        keyboard.Key.f8: "f8",
        keyboard.Key.f9: "f9",
        keyboard.Key.f10: "f10",
        keyboard.Key.f11: "f11",
        keyboard.Key.f12: "f12",
    }

    def __init__(
        self,
        combo: str,
        on_press_complete: Callable[[], None],
        on_release_break: Callable[[], None],
    ) -> None:
        self._required = _parse_combo(combo)
        self._on_press = on_press_complete
        self._on_release = on_release_break
        self._listener: Optional[keyboard.Listener] = None
        self._held: set[str] = set()
        self._engaged = False
        self._lock = threading.Lock()

    def _token(self, key) -> Optional[str]:
        if key in self._MOD_MAP:
            return self._MOD_MAP[key]
        if key in self._NAMED:
            return self._NAMED[key]
        if hasattr(key, "char") and key.char is not None:
            return key.char.lower()
        return None

    def _on_press_evt(self, key) -> None:
        token = self._token(key)
        if token is None:
            return
        with self._lock:
            self._held.add(token)
            should_fire = (
                not self._engaged
                and self._required.issubset(self._held)
            )
            if should_fire:
                self._engaged = True
        if should_fire:
            try:
                self._on_press()
            except Exception:
                pass

    def _on_release_evt(self, key) -> None:
        token = self._token(key)
        if token is None:
            return
        with self._lock:
            self._held.discard(token)
            should_fire = (
                self._engaged
                and not self._required.issubset(self._held)
            )
            if should_fire:
                self._engaged = False
        if should_fire:
            try:
                self._on_release()
            except Exception:
                pass

    def start(self) -> None:
        self._listener = keyboard.Listener(
            on_press=self._on_press_evt, on_release=self._on_release_evt
        )
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class GlobalHotkey:
    def __init__(
        self,
        combo: str,
        on_trigger: Callable[[], None],
        mode: str = "toggle",
        on_release: Optional[Callable[[], None]] = None,
    ) -> None:
        self.combo = combo
        self.mode = mode
        self.on_trigger = on_trigger
        self.on_release = on_release or (lambda: None)
        self._listener = None  # GlobalHotKeys (toggle) or _HoldHotkey (hold)

    def start(self) -> None:
        if self.mode == "hold":
            self._listener = _HoldHotkey(
                self.combo, self.on_trigger, self.on_release
            )
            self._listener.start()
        else:
            self._listener = keyboard.GlobalHotKeys({self.combo: self.on_trigger})
            self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def update(self, combo: str, mode: Optional[str] = None) -> None:
        self.stop()
        self.combo = combo
        if mode:
            self.mode = mode
        self.start()
