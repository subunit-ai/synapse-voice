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
from .. import hardware as _hw
from ..config import Config
from ..transcriber import ALL_MODES, CLOUD_MODES, mode_label
from .widgets import AnimatedToggle, BrandLogo

LOCAL_MODELS = ["base", "small", "medium", "large-v3"]

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

        # Row 1: big "Process locally" toggle. Default-on = highest privacy,
        # no data leaves the machine. When OFF, the cloud-provider picker
        # in Row 2 becomes active.
        local_row = QHBoxLayout()
        local_row.setSpacing(12)
        local_text = QVBoxLayout()
        local_text.setSpacing(2)
        local_title = QLabel("Process locally")
        local_title.setStyleSheet(f"color: {WHITE}; font-weight: 600;")
        local_text.addWidget(local_title)
        local_hint = QLabel(
            "Highest privacy — audio never leaves your machine. "
            "Disable to use a cloud provider instead."
        )
        local_hint.setObjectName("dim")
        local_hint.setWordWrap(True)
        local_text.addWidget(local_hint)
        local_row.addLayout(local_text, 1)
        self.local_toggle = AnimatedToggle(checked=(config.mode == "local"))
        self.local_toggle.toggled.connect(self._on_local_toggled)
        local_row.addWidget(self.local_toggle, 0, Qt.AlignmentFlag.AlignTop)
        ctrl_l.addLayout(local_row)

        # Row 1b: local-model picker — only meaningful while the toggle is on.
        # Lives in Quick Controls (not Settings) because TJ uses it constantly.
        hw = _hw.detect()
        recommended = _hw.recommend_local_model(hw)
        model_row = QHBoxLayout()
        model_row.setSpacing(10)
        model_row.setContentsMargins(0, 0, 0, 0)
        self.local_model_lbl = QLabel("Local model")
        self.local_model_lbl.setObjectName("dim")
        model_row.addWidget(self.local_model_lbl)
        self.local_model_combo = QComboBox()
        for m in LOCAL_MODELS:
            label = m
            if m == recommended:
                label += "  ⭐ recommended for your hardware"
            self.local_model_combo.addItem(label, m)
        idx = self.local_model_combo.findData(config.local_model)
        if idx >= 0:
            self.local_model_combo.setCurrentIndex(idx)
        self.local_model_combo.currentIndexChanged.connect(self._on_local_model_changed)
        model_row.addWidget(self.local_model_combo)
        model_row.addStretch()
        hw_summary = QLabel(_hw.describe(hw))
        hw_summary.setObjectName("dim")
        model_row.addWidget(hw_summary)
        ctrl_l.addLayout(model_row)

        # Row 2: cloud-provider picker — only enabled when local toggle is off.
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("Cloud provider"))
        self.mode_combo = QComboBox()
        for mode_id in CLOUD_MODES:
            label = mode_label(mode_id)
            if mode_id == "subunit":
                label += "  ·  Recommended"
            self.mode_combo.addItem(label, mode_id)
        # If config.mode is local, fall back to last_cloud_mode for the picker.
        cloud_mode = config.mode if config.mode in CLOUD_MODES else config.last_cloud_mode
        idx = self.mode_combo.findData(cloud_mode)
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
        # Apply initial enabled state — exactly one of the two pickers is live.
        self.mode_combo.setEnabled(config.mode != "local")
        self.local_model_combo.setEnabled(config.mode == "local")
        self.local_model_lbl.setEnabled(config.mode == "local")

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
        """Re-sync toggle/combos with config (after Settings dialog applied)."""
        is_local = self.config.mode == "local"
        # Local toggle
        if self.local_toggle.isChecked() != is_local:
            self.local_toggle.blockSignals(True)
            self.local_toggle.setChecked(is_local)
            self.local_toggle.blockSignals(False)
        # Cloud-mode combo (only when not local)
        target_cloud = self.config.mode if self.config.mode in CLOUD_MODES else self.config.last_cloud_mode
        c_idx = self.mode_combo.findData(target_cloud)
        if c_idx >= 0 and c_idx != self.mode_combo.currentIndex():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(c_idx)
            self.mode_combo.blockSignals(False)
        # Local-model combo
        m_idx = self.local_model_combo.findData(self.config.local_model)
        if m_idx >= 0 and m_idx != self.local_model_combo.currentIndex():
            self.local_model_combo.blockSignals(True)
            self.local_model_combo.setCurrentIndex(m_idx)
            self.local_model_combo.blockSignals(False)
        # Enabled state
        self.mode_combo.setEnabled(not is_local)
        self.local_model_combo.setEnabled(is_local)
        self.local_model_lbl.setEnabled(is_local)

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
            self.config.last_cloud_mode = m
            self._on_change_mode(m)

    def _on_local_toggled(self, checked: bool) -> None:
        self.mode_combo.setEnabled(not checked)
        self.local_model_combo.setEnabled(checked)
        self.local_model_lbl.setEnabled(checked)
        if checked:
            self._on_change_mode("local")
        else:
            target = self.mode_combo.currentData() or self.config.last_cloud_mode or "subunit"
            self._on_change_mode(target)

    def _on_local_model_changed(self, _idx: int) -> None:
        m = self.local_model_combo.currentData()
        if not m or m == self.config.local_model:
            return
        self.config.local_model = m
        self.config.save()
        # Re-apply local mode so the transcriber cache is invalidated and the
        # new model loads on the next hotkey press.
        if self.config.mode == "local":
            self._on_change_mode("local")

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
