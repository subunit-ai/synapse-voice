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

        # Hold menu as instance attribute — without it, Windows can GC the menu after __init__.
        self._menu = QMenu()
        self._record_action = QAction("Toggle Record", self._menu)
        self._record_action.triggered.connect(lambda: on_toggle_record())
        self._menu.addAction(self._record_action)

        self._menu.addSeparator()

        self._mode_menu = self._menu.addMenu("Mode")
        self._mode_group = QActionGroup(self._menu)
        self._mode_group.setExclusive(True)
        for mode_id, label in (
            ("local", "Local (faster-whisper)"),
            ("openrouter", "Cloud — OpenRouter"),
            ("subunit", "Cloud — Subunit (DSGVO, Phase 3)"),
        ):
            act = QAction(label, self._mode_menu, checkable=True)
            act.setData(mode_id)
            act.setChecked(mode_id == current_mode)
            act.triggered.connect(lambda _checked, m=mode_id: on_change_mode(m))
            self._mode_group.addAction(act)
            self._mode_menu.addAction(act)

        self._menu.addSeparator()
        self._history_action = QAction("History…", self._menu)
        self._history_action.triggered.connect(lambda: on_open_history())
        self._menu.addAction(self._history_action)

        self._settings_action = QAction("Settings…", self._menu)
        self._settings_action.triggered.connect(lambda: on_open_settings())
        self._menu.addAction(self._settings_action)

        self._menu.addSeparator()
        self._quit_action = QAction("Quit", self._menu)
        self._quit_action.triggered.connect(lambda: on_quit())
        self._menu.addAction(self._quit_action)

        self.setContextMenu(self._menu)

        # Windows-friendly: left-click on the tray icon also pops the menu.
        # On Linux/macOS the right-click default already works.
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: "QSystemTrayIcon.ActivationReason") -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self._menu.popup(self.geometry().center())

    def set_state(self, state: str, tooltip: str | None = None) -> None:
        if state in self._icons:
            self.setIcon(self._icons[state])
        if tooltip:
            self.setToolTip(f"Synapse Voice — {tooltip}")

    def set_mode(self, mode: str) -> None:
        for act in self._mode_group.actions():
            act.setChecked(act.data() == mode)
