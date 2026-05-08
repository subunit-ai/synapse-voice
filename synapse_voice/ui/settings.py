"""Settings dialog — v0.2.5 redesign with tabs + animated toggles + brand polish."""
from __future__ import annotations

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
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, autostart
from ..config import Config
from ..logger import log_file_path
from ..transcriber import ALL_MODES, mode_label
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
    """Label + animated toggle in a horizontal row."""

    def __init__(self, label: str, hint: str, checked: bool) -> None:
        from PyQt6.QtWidgets import QSizePolicy

        super().__init__()
        # Force the row to size itself based on its layout contents — without
        # this Qt sometimes collapses the row to one line and overlaps the
        # hint with the next row's title.
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)

        h = QHBoxLayout(self)
        h.setContentsMargins(0, 6, 0, 6)
        h.setSpacing(12)
        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        title = QLabel(label)
        title.setStyleSheet(f"color: {WHITE}; font-weight: 500;")
        title.setWordWrap(False)
        text_box.addWidget(title)
        if hint:
            hint_lbl = _hint(hint)
            text_box.addWidget(hint_lbl)
        h.addLayout(text_box, 1)
        self.toggle = AnimatedToggle(checked=checked)
        h.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignTop)

    def is_on(self) -> bool:
        return self.toggle.isChecked()


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Synapse Voice — Settings")
        self.setStyleSheet(DARK_QSS)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setMinimumSize(640, 580)
        self.resize(680, 640)
        self.config = config

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 26, 28, 22)
        outer.setSpacing(18)

        # Header with brand
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(BrandLogo(size=42))
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

        self.row_autostart = _ToggleRow(
            "Start automatically with system",
            "Adds a registry entry on Windows / .desktop file on Linux.",
            autostart.is_enabled(),
        )
        layout.addWidget(self.row_autostart)

        layout.addStretch(1)
        return page

    # ── Tab 2: Transcription ───────────────────────────────────────────────

    def _build_transcription_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(14)

        layout.addWidget(_section_title("Mode"))
        self.mode_combo = QComboBox()
        for mode_id in ALL_MODES:
            label = mode_label(mode_id)
            if mode_id == "subunit":
                label += "  ·  Recommended"
            self.mode_combo.addItem(label, mode_id)
        idx = self.mode_combo.findData(self.config.mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self.mode_combo)
        layout.addWidget(_hint(
            "Local runs entirely on your machine. Subunit uses our DSGVO-compliant "
            "server. OpenAI / Groq / Custom let you bring your own provider."
        ))

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

        # Activate the right panel for the current mode.
        self._on_mode_changed(self.mode_combo.currentIndex())
        return page

    def _build_local_panel(self) -> None:
        panel = QWidget()
        panel.setObjectName("tabPage")
        f = QFormLayout(panel)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)
        self.local_model_combo = QComboBox()
        for m in ["base", "small", "medium", "large-v3"]:
            self.local_model_combo.addItem(m, m)
        idx = self.local_model_combo.findData(self.config.local_model)
        if idx >= 0:
            self.local_model_combo.setCurrentIndex(idx)
        f.addRow("Model", self.local_model_combo)
        f.addRow("", _hint(
            "Larger models are more accurate but slower and use more RAM. "
            "First use of each model downloads ~150MB - 1.5GB."
        ))
        self.provider_stack.addWidget(panel)
        self._panel_index = {"local": 0}

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
        mode = self.mode_combo.currentData() or "local"
        self.provider_stack.setCurrentIndex(self._panel_index.get(mode, 0))

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
        config.mode = self.mode_combo.currentData() or "local"
        config.local_model = self.local_model_combo.currentData() or "base"
        config.language = self.lang_edit.text().strip() or "de"
        config.autopaste = self.row_autopaste.is_on()
        config.target_lock = self.row_target_lock.is_on()
        config.show_bubble = self.row_show_bubble.is_on()

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
