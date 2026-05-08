"""Settings dialog — v0.2.5 redesign with tabs + animated toggles + brand polish."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, autostart
from .. import account as _account_api
from ..config import Config
from ..logger import log_file_path
from ..transcriber import ALL_MODES, CLOUD_MODES, mode_label
from ..transcriber.cloud import PROVIDER_PRESETS
from .hotkey_capture import HotkeyCaptureButton
from .widgets import AnimatedToggle, BrandLogo

CYAN = "#40d6ff"
NIGHT = "#020817"
NIGHT_2 = "#0c1828"
NIGHT_BORDER = "#1f3145"
WHITE = "#e6f2fb"
WHITE_DIM = "#9fb1bd"

DARK_QSS = f"""
QDialog, QWidget#tabPage {{
    background: {NIGHT};
    color: {WHITE};
    font-size: 14px;
}}
QLabel {{ color: {WHITE}; }}
QLabel#dim {{ color: {WHITE_DIM}; font-size: 12px; }}
QLabel#sectionTitle {{
    color: {WHITE_DIM};
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1.5px;
}}
QLabel#h1 {{ font-size: 22px; font-weight: 600; }}

QLineEdit, QComboBox, QTextEdit {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 8px;
    padding: 8px 10px;
    selection-background-color: {CYAN};
    selection-color: {NIGHT};
}}
QLineEdit:focus, QComboBox:focus, QTextEdit:focus {{ border-color: {CYAN}; }}
QComboBox::drop-down {{ border: none; padding-right: 8px; }}
QComboBox QAbstractItemView {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    selection-background-color: #143246;
    selection-color: {WHITE};
}}

QPushButton {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 8px;
    padding: 8px 16px;
    min-width: 80px;
}}
QPushButton:hover {{ border-color: {CYAN}; }}
QPushButton#primary {{
    background: {CYAN};
    color: {NIGHT};
    border: none;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #6cdfff; }}
QPushButton#link {{
    background: transparent;
    border: none;
    color: {CYAN};
    text-decoration: underline;
    padding: 2px 0;
    min-width: 0;
}}

QTabWidget::pane {{
    background: transparent;
    border: 0;
}}
QTabBar::tab {{
    background: transparent;
    color: {WHITE_DIM};
    padding: 10px 22px;
    margin-right: 2px;
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    font-weight: 500;
}}
QTabBar::tab:hover {{ color: {WHITE}; }}
QTabBar::tab:selected {{
    color: {CYAN};
    border-bottom: 2px solid {CYAN};
}}

QFrame#card {{
    background: {NIGHT_2};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 12px;
}}
"""


def _section_title(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("sectionTitle")
    return lbl


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("dim")
    lbl.setWordWrap(True)
    return lbl


class _ToggleRow(QWidget):
    """Label + animated toggle in a horizontal row.

    Sizes itself to the hint label's wrapped height so a 2-line hint
    doesn't get clipped by the row above (the v0.3.10 "Behaviour" cutoff
    bug TJ flagged was the QSizePolicy.MinimumExpanding fallback being
    miscomputed before word-wrap had run).
    """

    def __init__(self, label: str, hint: str, checked: bool) -> None:
        from PyQt6.QtWidgets import QSizePolicy

        super().__init__()
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 4, 0, 4)
        h.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        title = QLabel(label)
        title.setStyleSheet(f"color: {WHITE}; font-weight: 500;")
        title.setWordWrap(False)
        text_box.addWidget(title)
        self._hint_lbl: Optional[QLabel] = None
        if hint:
            hint_lbl = _hint(hint)
            hint_lbl.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding
            )
            text_box.addWidget(hint_lbl)
            self._hint_lbl = hint_lbl
        h.addLayout(text_box, 1)
        self.toggle = AnimatedToggle(checked=checked)
        h.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)

    def is_on(self) -> bool:
        return self.toggle.isChecked()

    def resizeEvent(self, e) -> None:
        # Re-pin the row's minimum height after a width change so a wrapped
        # hint never clips into the next row.
        super().resizeEvent(e)
        if self._hint_lbl is not None:
            available_w = max(100, self.width() - 70)  # toggle + spacing
            wrapped_h = self._hint_lbl.heightForWidth(available_w)
            if wrapped_h > 0:
                # title (~18) + spacing (2) + hint + margins (8)
                self.setMinimumHeight(18 + 2 + wrapped_h + 8)


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Synapse Voice — Settings")
        self.setStyleSheet(DARK_QSS)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        # Size hint — comfortably fits the General tab on a typical 1080p
        # screen without dwarfing it. User can resize.
        self.setMinimumSize(560, 460)
        self.resize(620, 540)
        self.config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 22)
        outer.setSpacing(18)

        # Header with brand
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(BrandLogo(size=52))
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        h1 = QLabel("Settings")
        h1.setObjectName("h1")
        title_box.addWidget(h1)
        title_box.addWidget(_hint(f"Synapse Voice  ·  v{__version__}"))
        head.addLayout(title_box, 1)
        outer.addLayout(head)

        # Tabs
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        self.tabs.addTab(self._build_general_tab(), "General")
        self.tabs.addTab(self._build_transcription_tab(), "Transcription")
        self.tabs.addTab(self._build_vocabulary_tab(), "Vocabulary")
        self.tabs.addTab(self._build_overlay_tab(), "Overlay")
        self.tabs.addTab(self._build_account_tab(), "Account")
        self.tabs.addTab(self._build_about_tab(), "About")

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ── Tab 1: General ─────────────────────────────────────────────────────

    def _build_general_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Hotkey"))
        self.hotkey_btn = HotkeyCaptureButton(self.config.hotkey)
        layout.addWidget(self.hotkey_btn)
        layout.addWidget(_hint("Click and press the key combo you want to use to start/stop recording."))

        layout.addSpacing(6)
        layout.addWidget(_section_title("Language"))
        self.lang_edit = QLineEdit(self.config.language)
        self.lang_edit.setPlaceholderText("de, en, fr, ...")
        layout.addWidget(self.lang_edit)
        layout.addWidget(_hint("ISO-639-1 code. Pass an empty string to auto-detect (slower)."))

        layout.addSpacing(6)
        layout.addWidget(_section_title("Behaviour"))
        self.row_autopaste = _ToggleRow(
            "Auto-paste into target window",
            "After transcription, send Ctrl+V into the app you were using.",
            self.config.autopaste,
        )
        layout.addWidget(self.row_autopaste)

        self.row_target_lock = _ToggleRow(
            "Lock target window at hotkey-press",
            "Remember which window you were in when you pressed the hotkey.",
            self.config.target_lock,
        )
        layout.addWidget(self.row_target_lock)

        self.row_show_bubble = _ToggleRow(
            "Show floating status bubble",
            "Visual feedback while recording / transcribing.",
            self.config.show_bubble,
        )
        layout.addWidget(self.row_show_bubble)

        self.row_sound = _ToggleRow(
            "Sound feedback on hotkey",
            "Soft tap when recording starts, gentle pop when transcription "
            "lands. Volume tuned to be unobtrusive.",
            self.config.sound_enabled,
        )
        layout.addWidget(self.row_sound)

        self.row_orb_overlay = _ToggleRow(
            "Use Orb overlay (v0.4 preview)",
            "Persistent floating glass-spheres widget with hover-picker for "
            "language / cleanup-style / local-toggle. Replaces the bubble. "
            "Requires app restart to take effect.",
            self.config.use_orb_overlay,
        )
        layout.addWidget(self.row_orb_overlay)

        self.row_autostart = _ToggleRow(
            "Start automatically with system",
            "Adds a registry entry on Windows / .desktop file on Linux.",
            autostart.is_enabled(),
        )
        layout.addWidget(self.row_autostart)

        layout.addSpacing(6)
        layout.addWidget(_section_title("App language"))
        self.ui_language_combo = QComboBox()
        self.ui_language_combo.addItem("Deutsch", "de")
        self.ui_language_combo.addItem("English", "en")
        idx = self.ui_language_combo.findData(self.config.ui_language or "de")
        if idx >= 0:
            self.ui_language_combo.setCurrentIndex(idx)
        layout.addWidget(self.ui_language_combo)
        layout.addWidget(_hint(
            "Language used for the app's interface. Independent from the "
            "transcription language above."
        ))

        layout.addSpacing(6)
        layout.addWidget(_section_title("Microphone"))
        from ..recorder import list_input_devices
        from .mic_meter import MicLevelMeter

        self.mic_combo = QComboBox()
        self.mic_combo.addItem("System default", "")
        # Map device-name → index so the live meter can switch quickly.
        self._mic_name_to_index: dict[str, int] = {}
        for d in list_input_devices():
            self.mic_combo.addItem(d["name"], d["name"])
            self._mic_name_to_index[d["name"]] = d["index"]
        idx = self.mic_combo.findData(self.config.mic_device_name or "")
        if idx >= 0:
            self.mic_combo.setCurrentIndex(idx)
        layout.addWidget(self.mic_combo)

        self.mic_meter = MicLevelMeter()
        layout.addWidget(self.mic_meter)

        def _on_mic_pick(_i: int) -> None:
            name = self.mic_combo.currentData() or ""
            self.mic_meter.set_device(self._mic_name_to_index.get(name))

        self.mic_combo.currentIndexChanged.connect(_on_mic_pick)
        # Initial sync
        _on_mic_pick(0)

        layout.addWidget(_hint(
            "Pick the input device used for recording. The bar shows the "
            "live signal so you can verify the right mic is selected. "
            "Restart the app for the new device to apply to the hotkey."
        ))

        layout.addSpacing(6)
        layout.addWidget(_section_title("Recording mode"))
        self.recording_mode_combo = QComboBox()
        self.recording_mode_combo.addItem("Toggle — press to start, press again to stop", "toggle")
        self.recording_mode_combo.addItem("Hold — hold the hotkey, release to transcribe", "hold")
        idx = self.recording_mode_combo.findData(self.config.recording_mode)
        if idx >= 0:
            self.recording_mode_combo.setCurrentIndex(idx)
        layout.addWidget(self.recording_mode_combo)

        layout.addSpacing(6)
        layout.addWidget(_section_title("AI cleanup"))
        self.row_cleanup = _ToggleRow(
            "Clean up transcripts with AI",
            "Removes filler words, fixes punctuation, closes half-finished sentences. "
            "Routed through the subunit-server (extra ~0.5–1s).",
            self.config.cleanup_enabled,
        )
        layout.addWidget(self.row_cleanup)
        self.cleanup_style_combo = QComboBox()
        self.cleanup_style_combo.addItem("Tidy — light cleanup, keep wording", "tidy")
        self.cleanup_style_combo.addItem("Formal — rewrite into business tone", "formal")
        idx = self.cleanup_style_combo.findData(self.config.cleanup_style)
        if idx >= 0:
            self.cleanup_style_combo.setCurrentIndex(idx)
        layout.addWidget(self.cleanup_style_combo)

        layout.addSpacing(6)
        layout.addWidget(_section_title("Updates"))
        self.row_auto_update = _ToggleRow(
            "Check for updates on startup",
            "Asks GitHub once per launch for a newer release.",
            self.config.auto_update_check,
        )
        layout.addWidget(self.row_auto_update)

        layout.addStretch(1)
        return page

    # ── Tab 2: Transcription ───────────────────────────────────────────────

    def _build_transcription_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(14)

        # ── Big Local-vs-Cloud switch ──────────────────────────────────────
        layout.addWidget(_section_title("How to transcribe"))
        self.local_row = _ToggleRow(
            "Process locally",
            "Highest privacy — audio never leaves your machine. "
            "Disable to use a cloud provider instead.",
            self.config.mode == "local",
        )
        self.local_row.toggle.toggled.connect(self._on_local_toggled)
        layout.addWidget(self.local_row)

        # Cloud-provider picker (only relevant when local is off).
        self.cloud_lbl = QLabel("Cloud provider")
        self.cloud_lbl.setObjectName("dim")
        layout.addWidget(self.cloud_lbl)
        self.mode_combo = QComboBox()
        for mode_id in CLOUD_MODES:
            label = mode_label(mode_id)
            if mode_id == "subunit":
                label += "  ·  Recommended"
            self.mode_combo.addItem(label, mode_id)
        cloud_mode = (
            self.config.mode if self.config.mode in CLOUD_MODES else self.config.last_cloud_mode
        )
        idx = self.mode_combo.findData(cloud_mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.mode_combo)

        layout.addSpacing(8)
        layout.addWidget(_section_title("Provider settings"))

        self.provider_stack = QStackedWidget()
        layout.addWidget(self.provider_stack)

        self._build_local_panel()
        self._build_subunit_panel()
        self._build_openai_panel()
        self._build_groq_panel()
        self._build_custom_panel()

        layout.addStretch(1)

        # Activate the right panel + apply enabled-state.
        self._sync_transcription_panel()
        return page

    def _build_local_panel(self) -> None:
        from .. import hardware as _hw

        panel = QWidget()
        panel.setObjectName("tabPage")
        f = QFormLayout(panel)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        hw = _hw.detect()
        recommended = _hw.recommend_local_model(hw)
        self._recommended_model = recommended

        self.local_model_combo = QComboBox()
        for m in ["base", "small", "medium", "large-v3"]:
            label = m + ("  ⭐ recommended for your hardware" if m == recommended else "")
            self.local_model_combo.addItem(label, m)
        idx = self.local_model_combo.findData(self.config.local_model)
        if idx >= 0:
            self.local_model_combo.setCurrentIndex(idx)
        f.addRow("Model", self.local_model_combo)

        auto_btn = QPushButton(f"Use recommended ({recommended})")
        auto_btn.clicked.connect(self._apply_recommended_model)
        f.addRow("", auto_btn)

        f.addRow("", _hint(
            f"Detected hardware: {_hw.describe(hw)}.\n\n"
            "Larger models are more accurate but slower and use more RAM. "
            "First use of each model downloads ~150MB – 1.5GB."
        ))
        self.provider_stack.addWidget(panel)
        self._panel_index = {"local": 0}

    def _apply_recommended_model(self) -> None:
        idx = self.local_model_combo.findData(self._recommended_model)
        if idx >= 0:
            self.local_model_combo.setCurrentIndex(idx)

    def _build_subunit_panel(self) -> None:
        panel = QWidget()
        panel.setObjectName("tabPage")
        f = QFormLayout(panel)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        self.subunit_endpoint_edit = QLineEdit(self.config.subunit_endpoint)
        self.subunit_endpoint_edit.setPlaceholderText("https://transcribe.subunit.ai/v1/transcribe")
        f.addRow("Endpoint", self.subunit_endpoint_edit)

        self.subunit_key_edit = QLineEdit(self.config.subunit_api_key)
        self.subunit_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.subunit_key_edit.setPlaceholderText("sk-svc-... (provided by subunit)")
        f.addRow("API key", self.subunit_key_edit)

        f.addRow("", _hint(
            "Premium DSGVO-compliant transcription on the subunit-server."
        ))
        self.provider_stack.addWidget(panel)
        self._panel_index["subunit"] = 1

    def _build_openai_panel(self) -> None:
        panel = QWidget()
        panel.setObjectName("tabPage")
        f = QFormLayout(panel)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        self.openai_key_edit = QLineEdit(self.config.openai_api_key)
        self.openai_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.openai_key_edit.setPlaceholderText(PROVIDER_PRESETS["openai"]["key_hint"])
        f.addRow("API key", self.openai_key_edit)

        self.openai_model_edit = QLineEdit(self.config.openai_model)
        self.openai_model_edit.setPlaceholderText("whisper-1")
        f.addRow("Model", self.openai_model_edit)

        link = QPushButton("Get an OpenAI API key →")
        link.setObjectName("link")
        link.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(PROVIDER_PRESETS["openai"]["signup_url"]))
        )
        f.addRow("", link)
        self.provider_stack.addWidget(panel)
        self._panel_index["openai"] = 2

    def _build_groq_panel(self) -> None:
        panel = QWidget()
        panel.setObjectName("tabPage")
        f = QFormLayout(panel)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        self.groq_key_edit = QLineEdit(self.config.groq_api_key)
        self.groq_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.groq_key_edit.setPlaceholderText(PROVIDER_PRESETS["groq"]["key_hint"])
        f.addRow("API key", self.groq_key_edit)

        self.groq_model_edit = QLineEdit(self.config.groq_model)
        self.groq_model_edit.setPlaceholderText("whisper-large-v3-turbo")
        f.addRow("Model", self.groq_model_edit)

        link = QPushButton("Get a Groq API key (free tier) →")
        link.setObjectName("link")
        link.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(PROVIDER_PRESETS["groq"]["signup_url"]))
        )
        f.addRow("", link)
        self.provider_stack.addWidget(panel)
        self._panel_index["groq"] = 3

    def _build_custom_panel(self) -> None:
        panel = QWidget()
        panel.setObjectName("tabPage")
        f = QFormLayout(panel)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        self.custom_endpoint_edit = QLineEdit(self.config.custom_endpoint)
        self.custom_endpoint_edit.setPlaceholderText("https://your-server/v1/audio/transcriptions")
        f.addRow("Endpoint", self.custom_endpoint_edit)

        self.custom_key_edit = QLineEdit(self.config.custom_api_key)
        self.custom_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        f.addRow("API key", self.custom_key_edit)

        self.custom_model_edit = QLineEdit(self.config.custom_model)
        self.custom_model_edit.setPlaceholderText("whisper-1")
        f.addRow("Model", self.custom_model_edit)

        f.addRow("", _hint(
            "Any OpenAI-compatible /v1/audio/transcriptions endpoint."
        ))
        self.provider_stack.addWidget(panel)
        self._panel_index["custom"] = 4

    def _on_mode_changed(self, _idx: int) -> None:
        # The dropdown only has cloud providers now; switching it never enters
        # local mode — that's owned by the toggle above.
        self._sync_transcription_panel()

    def _on_local_toggled(self, _checked: bool) -> None:
        self._sync_transcription_panel()

    def _sync_transcription_panel(self) -> None:
        """Reflect the current toggle/dropdown state in panel + enabled-state."""
        is_local = self.local_row.is_on()
        self.mode_combo.setEnabled(not is_local)
        self.cloud_lbl.setEnabled(not is_local)
        if is_local:
            self.provider_stack.setCurrentIndex(self._panel_index["local"])
        else:
            mode = self.mode_combo.currentData() or "subunit"
            self.provider_stack.setCurrentIndex(self._panel_index.get(mode, 1))

    # ── Tab 3: Vocabulary (Lexikon) ────────────────────────────────────────

    def _build_vocabulary_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(10)

        layout.addWidget(_section_title("Lexikon"))
        layout.addWidget(_hint(
            "Add custom vocabulary so Whisper transcribes brand names, "
            "technical terms or proper nouns the way you want them. "
            "Both columns are required for an entry to take effect."
        ))

        self.vocab_table = QTableWidget()
        self.vocab_table.setColumnCount(2)
        self.vocab_table.setHorizontalHeaderLabels(["Sounds like", "Write as"])
        self.vocab_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.vocab_table.verticalHeader().setVisible(False)
        # Pre-fill from config
        existing = list(self.config.vocabulary or [])
        self.vocab_table.setRowCount(max(8, len(existing) + 2))
        for i, entry in enumerate(existing):
            self.vocab_table.setItem(
                i, 0, QTableWidgetItem(entry.get("sounds_like", ""))
            )
            self.vocab_table.setItem(
                i, 1, QTableWidgetItem(entry.get("write_as", ""))
            )
        layout.addWidget(self.vocab_table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_row = QPushButton("Add row")
        add_row.clicked.connect(
            lambda: self.vocab_table.setRowCount(self.vocab_table.rowCount() + 1)
        )
        btn_row.addWidget(add_row)
        clear_row = QPushButton("Remove selected")
        clear_row.clicked.connect(self._remove_vocab_row)
        btn_row.addWidget(clear_row)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        return page

    def _remove_vocab_row(self) -> None:
        row = self.vocab_table.currentRow()
        if row >= 0:
            self.vocab_table.removeRow(row)

    def _harvest_vocab(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self.vocab_table.rowCount()):
            sl_item = self.vocab_table.item(r, 0)
            wa_item = self.vocab_table.item(r, 1)
            sl = (sl_item.text().strip() if sl_item else "")
            wa = (wa_item.text().strip() if wa_item else "")
            if sl and wa:
                out.append({"sounds_like": sl, "write_as": wa})
        return out

    # ── Tab 4: Overlay ─────────────────────────────────────────────────────

    def _build_overlay_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Visual feedback"))
        # Re-use the toggle from General — referenced again here so this tab
        # is self-contained for users who jump straight to it.
        self.row_orb_overlay_v2 = _ToggleRow(
            "Use Orb overlay",
            "Persistent floating glass-spheres widget. Hover for satellite "
            "buttons (language / cleanup-style / local-toggle). "
            "Right-click and drag to move it.",
            self.config.use_orb_overlay,
        )
        layout.addWidget(self.row_orb_overlay_v2)

        layout.addSpacing(6)
        layout.addWidget(_section_title("Color theme"))
        self.orb_theme_combo = QComboBox()
        self.orb_theme_combo.addItem("Cyan (default)", "cyan")
        self.orb_theme_combo.addItem("Violet", "violet")
        self.orb_theme_combo.addItem("Mint", "mint")
        idx = self.orb_theme_combo.findData(self.config.orb_color_theme)
        if idx >= 0:
            self.orb_theme_combo.setCurrentIndex(idx)
        layout.addWidget(self.orb_theme_combo)

        layout.addSpacing(6)
        layout.addWidget(_section_title("Position"))
        self.orb_position_combo = QComboBox()
        self.orb_position_combo.addItem("Bottom-center (default)", "bottom-center")
        self.orb_position_combo.addItem("Bottom-right", "bottom-right")
        self.orb_position_combo.addItem("Bottom-left", "bottom-left")
        self.orb_position_combo.addItem("Top-center", "top-center")
        self.orb_position_combo.addItem("Top-right", "top-right")
        self.orb_position_combo.addItem("Top-left", "top-left")
        # If user has dragged the orb to a custom spot, show "Custom" and
        # don't overwrite it unless they pick a corner.
        cur_pos = self.config.orb_position or "bottom-center"
        if cur_pos.startswith("custom-"):
            self.orb_position_combo.addItem(f"Custom ({cur_pos[7:]})", cur_pos)
        idx = self.orb_position_combo.findData(cur_pos)
        if idx >= 0:
            self.orb_position_combo.setCurrentIndex(idx)
        layout.addWidget(self.orb_position_combo)
        layout.addWidget(_hint(
            "Tip: right-click + drag the orb to place it anywhere on screen — "
            "the position is saved automatically."
        ))

        layout.addSpacing(6)
        layout.addWidget(_section_title("Idle behaviour"))
        self.row_orb_pulse = _ToggleRow(
            "Subtle breathing pulse when idle",
            "Slow halo pulse signals the app is alive. Disable for a "
            "completely still orb when not recording.",
            self.config.orb_idle_pulse,
        )
        layout.addWidget(self.row_orb_pulse)

        layout.addStretch(1)
        return page

    # ── Tab 4: Account ─────────────────────────────────────────────────────

    def _build_account_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Subunit account"))

        self.account_status_lbl = QLabel("")
        self.account_status_lbl.setWordWrap(True)
        layout.addWidget(self.account_status_lbl)

        layout.addSpacing(6)
        form = QFormLayout()
        form.setSpacing(10)
        self.account_email_edit = QLineEdit(self.config.account_email)
        self.account_email_edit.setPlaceholderText("you@example.com")
        form.addRow("Email", self.account_email_edit)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.account_signin_btn = QPushButton("Get my Subunit key")
        self.account_signin_btn.setObjectName("primary")
        self.account_signin_btn.clicked.connect(self._on_account_signin)
        btn_row.addWidget(self.account_signin_btn)

        self.account_logout_btn = QPushButton("Sign out")
        self.account_logout_btn.clicked.connect(self._on_account_logout)
        btn_row.addWidget(self.account_logout_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        layout.addSpacing(8)
        layout.addWidget(_hint(
            "Type your email and click the button — we'll create your account "
            "and install your API key automatically. Same email always returns "
            "the same key, so re-installing the app just works.\n\n"
            "No password yet (coming in a future update)."
        ))

        layout.addStretch(1)
        self._refresh_account_status()
        return page

    def _refresh_account_status(self) -> None:
        if self.config.account_email and self.config.subunit_api_key:
            self.account_status_lbl.setText(
                f"<b>Signed in as</b> {self.config.account_email}"
            )
            self.account_signin_btn.setText("Refresh key")
            self.account_logout_btn.setEnabled(True)
        else:
            self.account_status_lbl.setText(
                "<b>No account yet.</b>  Enter your email below and click "
                "<i>Get my Subunit key</i> — we'll create the account and install "
                "your key in one step."
            )
            self.account_signin_btn.setText("Get my Subunit key")
            self.account_logout_btn.setEnabled(False)

    def _on_account_signin(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        email = self.account_email_edit.text().strip()
        if "@" not in email:
            QMessageBox.warning(self, "Synapse Voice", "Please enter a valid email.")
            return
        endpoint = (
            self.subunit_endpoint_edit.text().strip()
            or self.config.subunit_endpoint
        )
        try:
            acct = _account_api.sign_up(endpoint, email)
        except Exception as e:
            QMessageBox.critical(self, "Synapse Voice — sign-up failed", str(e))
            return
        # Persist immediately to config so the key is usable even if user clicks Cancel.
        self.config.account_email = acct.email
        self.config.subunit_api_key = acct.api_key
        self.config.subunit_endpoint = endpoint
        self.config.save()
        # Reflect in the Transcription tab fields too.
        self.subunit_key_edit.setText(acct.api_key)
        self.subunit_endpoint_edit.setText(endpoint)
        self._refresh_account_status()
        msg = (
            f"Welcome! Account created and key installed."
            if acct.is_new
            else f"Welcome back. Key refreshed."
        )
        QMessageBox.information(self, "Synapse Voice", msg)

    def _on_account_logout(self) -> None:
        self.config.account_email = ""
        self.config.subunit_api_key = ""
        self.config.save()
        self.account_email_edit.setText("")
        self.subunit_key_edit.setText("")
        self._refresh_account_status()

    # ── Tab 3: About ───────────────────────────────────────────────────────

    def _build_about_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(14)

        layout.addWidget(_section_title("About"))
        layout.addWidget(QLabel(f"Synapse Voice  v{__version__}"))
        layout.addWidget(_hint("Hotkey-driven speech-to-text for the subunit ecosystem."))

        layout.addSpacing(8)
        layout.addWidget(_section_title("Diagnostics"))
        layout.addWidget(QLabel(f"Log file: {log_file_path()}"))
        open_log_btn = QPushButton("Open log folder")
        open_log_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_file_path().parent)))
        )
        layout.addWidget(open_log_btn, 0, Qt.AlignmentFlag.AlignLeft)

        open_repo = QPushButton("github.com/subunit-ai/synapse-voice →")
        open_repo.setObjectName("link")
        open_repo.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://github.com/subunit-ai/synapse-voice"))
        )
        layout.addWidget(open_repo, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch(1)
        return page

    # ── Apply ──────────────────────────────────────────────────────────────

    def apply_to(self, config: Config) -> None:
        config.hotkey = self.hotkey_btn.value() or "<ctrl>+<shift>+<space>"
        # Mode is derived from the big local toggle; the dropdown only chooses
        # which cloud provider to use when local is off.
        cloud_choice = self.mode_combo.currentData() or "subunit"
        if self.local_row.is_on():
            config.mode = "local"
        else:
            config.mode = cloud_choice
        # Always remember the most-recent cloud pick so toggling local off
        # later returns to the same provider.
        config.last_cloud_mode = cloud_choice
        config.local_model = self.local_model_combo.currentData() or "base"
        config.language = self.lang_edit.text().strip() or "de"
        config.autopaste = self.row_autopaste.is_on()
        config.target_lock = self.row_target_lock.is_on()
        config.show_bubble = self.row_show_bubble.is_on()
        config.sound_enabled = self.row_sound.is_on()
        # The Orb toggle is mirrored on both General + Overlay tabs.
        # The Overlay tab is the "more complete" surface, so let it win
        # if the user touched it; otherwise fall back to the General tab.
        config.use_orb_overlay = (
            self.row_orb_overlay_v2.is_on()
            if hasattr(self, "row_orb_overlay_v2")
            else self.row_orb_overlay.is_on()
        )
        config.orb_color_theme = self.orb_theme_combo.currentData() or "cyan"
        config.orb_position = self.orb_position_combo.currentData() or "bottom-center"
        config.orb_idle_pulse = self.row_orb_pulse.is_on()
        config.recording_mode = self.recording_mode_combo.currentData() or "toggle"
        config.mic_device_name = self.mic_combo.currentData() or ""
        new_ui_lang = self.ui_language_combo.currentData() or "de"
        if new_ui_lang != config.ui_language:
            config.ui_language = new_ui_lang
            from .. import i18n
            i18n.set_language(new_ui_lang)
        # v0.3.9 Lexikon: harvest vocab table into list[dict]
        if hasattr(self, "vocab_table"):
            config.vocabulary = self._harvest_vocab()
        config.cleanup_enabled = self.row_cleanup.is_on()
        config.cleanup_style = self.cleanup_style_combo.currentData() or "tidy"
        config.auto_update_check = self.row_auto_update.is_on()

        config.subunit_endpoint = (
            self.subunit_endpoint_edit.text().strip()
            or "https://transcribe.subunit.ai/v1/transcribe"
        )
        config.subunit_api_key = self.subunit_key_edit.text().strip()
        config.openai_api_key = self.openai_key_edit.text().strip()
        config.openai_model = self.openai_model_edit.text().strip() or "whisper-1"
        config.groq_api_key = self.groq_key_edit.text().strip()
        config.groq_model = (
            self.groq_model_edit.text().strip() or "whisper-large-v3-turbo"
        )
        config.custom_endpoint = self.custom_endpoint_edit.text().strip()
        config.custom_api_key = self.custom_key_edit.text().strip()
        config.custom_model = self.custom_model_edit.text().strip() or "whisper-1"

        config.save()

        if self.row_autostart.is_on():
            autostart.enable()
        else:
            autostart.disable()
