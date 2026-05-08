"""System tray icon + menu."""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon


def _make_icon(color: QColor, size: int = 22) -> QIcon:
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    p.drawEllipse(2, 2, size - 4, size - 4)
    p.end()
    return QIcon(pix)


CYAN = QColor(64, 214, 255)
RED = QColor(255, 80, 80)
GREEN = QColor(80, 220, 130)
GRAY = QColor(140, 150, 160)


class Tray(QSystemTrayIcon):
    def __init__(
        self,
        on_toggle_record: Callable[[], None],
        on_open_settings: Callable[[], None],
        on_open_history: Callable[[], None],
        on_change_mode: Callable[[str], None],
        on_quit: Callable[[], None],
        current_mode: str,
    ) -> None:
        super().__init__()
        self._icons = {
            "idle": _make_icon(CYAN),
            "recording": _make_icon(RED),
            "transcribing": _make_icon(CYAN),
            "done": _make_icon(GREEN),
            "error": _make_icon(GRAY),
        }
        self.setIcon(self._icons["idle"])
        self.setToolTip("Synapse Voice — idle")

        menu = QMenu()
        self._record_action = QAction("Toggle Record", menu)
        self._record_action.triggered.connect(lambda: on_toggle_record())
        menu.addAction(self._record_action)

        menu.addSeparator()

        mode_menu = menu.addMenu("Mode")
        self._mode_group = QActionGroup(menu)
        self._mode_group.setExclusive(True)
        for mode_id, label in (
            ("local", "Local (faster-whisper)"),
            ("openrouter", "Cloud — OpenRouter"),
            ("subunit", "Cloud — Subunit (DSGVO, Phase 3)"),
        ):
            act = QAction(label, mode_menu, checkable=True)
            act.setData(mode_id)
            act.setChecked(mode_id == current_mode)
            act.triggered.connect(lambda _checked, m=mode_id: on_change_mode(m))
            self._mode_group.addAction(act)
            mode_menu.addAction(act)

        menu.addSeparator()
        history_action = QAction("History…", menu)
        history_action.triggered.connect(lambda: on_open_history())
        menu.addAction(history_action)

        settings_action = QAction("Settings…", menu)
        settings_action.triggered.connect(lambda: on_open_settings())
        menu.addAction(settings_action)

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(lambda: on_quit())
        menu.addAction(quit_action)

        self.setContextMenu(menu)

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        if state in self._icons:
            self.setIcon(self._icons[state])
        if tooltip:
            self.setToolTip(f"Synapse Voice — {tooltip}")

    def set_mode(self, mode: str) -> None:
        for act in self._mode_group.actions():
            act.setChecked(act.data() == mode)
