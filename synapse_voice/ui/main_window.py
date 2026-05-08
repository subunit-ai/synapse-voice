"""Main application window — dashboard / control center.

A real top-level window so Synapse Voice feels like an installed app, not just a
tray gadget. Closing the window hides it; quitting goes through the tray menu.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..config import Config
from ..transcriber import ALL_MODES, mode_label
from .widgets import BrandLogo

CYAN = "#40d6ff"
NIGHT = "#020817"
NIGHT_2 = "#0c1828"
NIGHT_BORDER = "#1f3145"
WHITE = "#e6f2fb"
WHITE_DIM = "#9fb1bd"

QSS = f"""
QMainWindow, QWidget#central {{
    background: {NIGHT};
    color: {WHITE};
}}
QLabel {{ color: {WHITE}; }}
QLabel#dim {{ color: {WHITE_DIM}; }}
QLabel#h1 {{ font-size: 22px; font-weight: 600; }}
QLabel#h2 {{ font-size: 14px; font-weight: 500; color: {WHITE_DIM}; letter-spacing: 1px; }}
QLabel#big {{ font-size: 28px; font-weight: 600; color: {CYAN}; }}
QLabel#statusBig {{ font-size: 16px; font-weight: 500; }}
QFrame#card {{
    background: {NIGHT_2};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 14px;
}}
QPushButton {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 8px;
    padding: 8px 14px;
    min-width: 100px;
}}
QPushButton:hover {{ border-color: {CYAN}; }}
QPushButton#primary {{
    background: {CYAN};
    color: {NIGHT};
    border: none;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #6cdfff; }}
QComboBox {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    min-width: 220px;
}}
QListWidget {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 10px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 8px 10px;
    border-bottom: 1px solid {NIGHT_BORDER};
}}
QListWidget::item:selected {{
    background: #143246;
    color: {WHITE};
}}
"""


def _format_seconds(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: Config,
        on_change_mode: Callable[[str], None],
        on_open_settings: Callable[[], None],
        on_open_history: Callable[[], None],
        on_quit: Callable[[], None],
    ) -> None:
        super().__init__()
        self.config = config
        self._on_change_mode = on_change_mode
        self._on_open_settings = on_open_settings
        self._on_open_history = on_open_history
        self._on_quit = on_quit

        self.setWindowTitle("Synapse Voice")
        self.setStyleSheet(QSS)
        self.setMinimumSize(640, 520)
        self.resize(720, 600)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(24, 22, 24, 22)
        outer.setSpacing(16)

        # ── Header ─────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(BrandLogo(size=56))
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Synapse Voice")
        title.setObjectName("h1")
        version = QLabel(f"v{__version__}")
        version.setObjectName("dim")
        title_box.addWidget(title)
        title_box.addWidget(version)
        header.addLayout(title_box)
        header.addStretch()
        # Status is now reflected only in the bubble + tray icon. The redundant
        # "● idle" pill in the header looked like a notification badge and TJ
        # wanted it gone.
        self.status_lbl = QLabel("")
        self.status_lbl.setVisible(False)
        outer.addLayout(header)

        # ── Stats row ──────────────────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_count = self._make_stat_card(
            "TRANSCRIBED", str(config.total_transcriptions)
        )
        stats_row.addWidget(self.stat_count)
        self.stat_audio = self._make_stat_card(
            "AUDIO PROCESSED", _format_seconds(config.total_audio_seconds)
        )
        stats_row.addWidget(self.stat_audio)
        self.stat_saved = self._make_stat_card(
            "TIME SAVED",
            _format_seconds(config.total_audio_seconds * 2.5),
        )
        stats_row.addWidget(self.stat_saved)
        outer.addLayout(stats_row)

        # ── Quick controls card ────────────────────────────────────────────
        ctrl = self._make_card()
        ctrl_l = QVBoxLayout(ctrl)
        ctrl_l.setContentsMargins(18, 16, 18, 16)
        ctrl_l.setSpacing(10)

        ctrl_title = QLabel("Quick controls")
        ctrl_title.setObjectName("h2")
        ctrl_l.addWidget(ctrl_title)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("Mode"))
        self.mode_combo = QComboBox()
        for mode_id in ALL_MODES:
            label = mode_label(mode_id)
            if mode_id == "subunit":
                label += "  ·  Recommended"
            self.mode_combo.addItem(label, mode_id)
        idx = self.mode_combo.findData(config.mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(self.mode_combo)
        row.addStretch()

        hotkey_lbl = QLabel(f"Hotkey: {config.hotkey}")
        hotkey_lbl.setObjectName("dim")
        self.hotkey_lbl = hotkey_lbl
        row.addWidget(hotkey_lbl)
        ctrl_l.addLayout(row)

        outer.addWidget(ctrl)

        # ── Recent transcriptions ──────────────────────────────────────────
        recent_title = QLabel("Recent transcriptions")
        recent_title.setObjectName("h2")
        outer.addWidget(recent_title)

        self.history_list = QListWidget()
        self.history_list.setSelectionMode(self.history_list.SelectionMode.SingleSelection)
        self.history_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        outer.addWidget(self.history_list, 1)

        # ── Footer ─────────────────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(10)
        history_btn = QPushButton("Full history…")
        history_btn.clicked.connect(lambda: self._on_open_history())
        settings_btn = QPushButton("Settings…")
        settings_btn.clicked.connect(lambda: self._on_open_settings())
        hide_btn = QPushButton("Hide to tray")
        hide_btn.clicked.connect(self.hide)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(lambda: self._on_quit())
        footer.addWidget(history_btn)
        footer.addWidget(settings_btn)
        footer.addStretch()
        footer.addWidget(hide_btn)
        footer.addWidget(quit_btn)
        outer.addLayout(footer)

        # Periodically refresh stats while window is open
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    # ── public API ─────────────────────────────────────────────────────────

    def set_status(self, label: str, color: str = CYAN) -> None:
        # No-op kept for callsite compatibility — status now lives in the
        # tray icon + floating bubble only.
        return

    def refresh_mode(self) -> None:
        """Re-sync the mode combo with config (after Settings dialog applied)."""
        idx = self.mode_combo.findData(self.config.mode)
        if idx >= 0 and idx != self.mode_combo.currentIndex():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(idx)
            self.mode_combo.blockSignals(False)

    def refresh(self) -> None:
        self.stat_count.findChild(QLabel, "value").setText(
            str(self.config.total_transcriptions)
        )
        self.stat_audio.findChild(QLabel, "value").setText(
            _format_seconds(self.config.total_audio_seconds)
        )
        self.stat_saved.findChild(QLabel, "value").setText(
            _format_seconds(self.config.total_audio_seconds * 2.5)
        )
        self.hotkey_lbl.setText(f"Hotkey: {self.config.hotkey}")

        # Refill recent list (top 10, newest first)
        self.history_list.clear()
        recent = list(reversed(self.config.history[-10:]))
        if not recent:
            empty = QListWidgetItem("No transcriptions yet — press your hotkey to begin")
            empty.setForeground(QColor(WHITE_DIM))
            self.history_list.addItem(empty)
            return
        for entry in recent:
            text = entry.get("text", "")
            ts = entry.get("ts", "")
            mode = entry.get("mode", "")
            line1 = text if len(text) <= 100 else text[:97] + "…"
            label = f"{line1}\n{ts} · {mode}"
            it = QListWidgetItem(label)
            self.history_list.addItem(it)

    # ── private ────────────────────────────────────────────────────────────

    def _on_mode_changed(self, _idx: int) -> None:
        m = self.mode_combo.currentData()
        if m:
            self._on_change_mode(m)

    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    def _make_stat_card(self, title: str, value: str) -> QFrame:
        card = self._make_card()
        l = QVBoxLayout(card)
        l.setContentsMargins(18, 16, 18, 16)
        l.setSpacing(4)
        t = QLabel(title)
        t.setObjectName("h2")
        v = QLabel(value)
        v.setObjectName("big")
        v.setProperty("class", "value")
        v.setObjectName("value")  # findChild lookup
        l.addWidget(t)
        l.addWidget(v)
        return card

    def closeEvent(self, e) -> None:
        # Hide instead of quit — feels more like an installed app.
        e.ignore()
        self.hide()
