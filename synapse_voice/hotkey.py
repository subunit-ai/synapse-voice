"""Global hotkey listener via pynput, marshalled to Qt signals."""
from __future__ import annotations

from typing import Callable

from pynput import keyboard


class GlobalHotkey:
    def __init__(self, combo: str, on_trigger: Callable[[], None]) -> None:
        self.combo = combo
        self.on_trigger = on_trigger
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        self._listener = keyboard.GlobalHotKeys({self.combo: self.on_trigger})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None

    def update(self, combo: str) -> None:
        self.stop()
        self.combo = combo
        self.start()
