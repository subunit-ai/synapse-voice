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
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, auto_mode as _auto_mode, autostart
from .. import account as _account_api
from ..config import Config
from ..logger import log_file_path
from ..transcriber import ALL_MODES, CLOUD_MODES, mode_label
from ..transcriber.cloud import PROVIDER_PRESETS
from .hotkey_capture import HotkeyCaptureButton, detect_hotkey_conflict
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


# v0.9.13 (Codex P1): redaction helpers for the diagnostics dump. Log
# files often contain headers / URLs with tokens, transcribed text, and
# auth-error bodies — and the diagnostics block is explicitly meant to
# be shareable with support. Each pattern matches a known-sensitive
# format and replaces it with a length-preserving placeholder so the
# user can still see WHERE in the log the redaction happened (useful
# when debugging "why is my API call failing").
import re as _re_redact

_REDACT_PATTERNS = (
    # Authorization: Bearer eyJ...  /  Authorization: Token xxx
    (_re_redact.compile(r"(?i)(authorization\s*:\s*)(\S+)"),                 r"\1[REDACTED-AUTH]"),
    # X-API-Key: deadbeef
    (_re_redact.compile(r"(?i)(x-api-key\s*:\s*)(\S+)"),                     r"\1[REDACTED-KEY]"),
    # access_token / refresh_token / api_key in URLs or JSON
    (_re_redact.compile(r"(?i)\"(access_token|refresh_token|api_key|password|secret|token)\"\s*:\s*\"[^\"]*\""),
                                                                              r'"\1": "[REDACTED]"'),
    (_re_redact.compile(r"(?i)(access_token|refresh_token|api_key|token|secret)=([^\s&]+)"),
                                                                              r"\1=[REDACTED]"),
    # JWTs anywhere (eyJ... 3-part base64url with dots)
    (_re_redact.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"),
                                                                              "[REDACTED-JWT]"),
    # email addresses — not strictly secrets but commonly identifying
    (_re_redact.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
                                                                              "[REDACTED-EMAIL]"),
)


def _redact_log_line(line: str) -> str:
    for pat, repl in _REDACT_PATTERNS:
        line = pat.sub(repl, line)
    return line


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
        self.setWindowTitle("Sonar — Settings")
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
        title_box.addWidget(_hint(f"Sonar  ·  v{__version__}"))
        head.addLayout(title_box, 1)
        outer.addLayout(head)

        # Tabs
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        # TJ-Feedback 2026-05-16: tabs were not scrollable, so on smaller
        # windows (or tabs with a lot of content like Auto-Mode and
        # Vocabulary) the bottom controls were unreachable. Wrap every
        # tab page in a QScrollArea so the dialog can stay compact while
        # any tab can grow as tall as it needs to.
        self.tabs.addTab(self._wrap_scroll(self._build_general_tab()),       "General")
        self.tabs.addTab(self._wrap_scroll(self._build_transcription_tab()), "Transcription")
        self.tabs.addTab(self._wrap_scroll(self._build_vocabulary_tab()),    "Vocabulary")
        self.tabs.addTab(self._wrap_scroll(self._build_auto_mode_tab()),     "Auto-Mode")
        self.tabs.addTab(self._wrap_scroll(self._build_overlay_tab()),       "Overlay")
        self.tabs.addTab(self._wrap_scroll(self._build_account_tab()),       "Account")
        self.tabs.addTab(self._wrap_scroll(self._build_about_tab()),         "About")

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primary")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    # ── Tab scroll wrapper ─────────────────────────────────────────────────

    def _wrap_scroll(self, page: QWidget) -> QScrollArea:
        """Wrap a tab page in a QScrollArea so its content is always reachable.

        TJ-Feedback 2026-05-16: bottom-of-tab controls were unreachable on
        smaller dialog heights / smaller laptop screens. With this wrapper
        every tab gets a vertical scrollbar when the content overflows the
        tab's viewport height.

        Notes:
        • setWidgetResizable(True) makes the inner widget match the scroll
          area's width — so we don't get an unwanted horizontal scrollbar
          when the dialog is narrow.
        • frameShape Plain keeps the visual flush with the tab background.
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(page)
        return scroll

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
        self.hotkey_warning = QLabel("")
        self.hotkey_warning.setWordWrap(True)
        self.hotkey_warning.setStyleSheet(
            f"color: #ffb86b; font-size: 12px; padding: 4px 0;"
        )
        self.hotkey_warning.hide()
        layout.addWidget(self.hotkey_warning)
        self.hotkey_btn.captured.connect(self._refresh_hotkey_warning)
        self._refresh_hotkey_warning()

        layout.addSpacing(6)
        layout.addWidget(_section_title("Language"))
        self.lang_edit = QLineEdit(self.config.language)
        self.lang_edit.setPlaceholderText("de, en, fr, ... or 'auto'")
        layout.addWidget(self.lang_edit)
        layout.addWidget(_hint(
            "ISO-639-1 code (de, en, fr, ...) — or 'auto' to let Whisper detect "
            "the language per recording. 'auto' is best for mixed-language calls "
            "(slightly slower, ~5%)."
        ))

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
        layout.addWidget(_section_title("Theme"))
        self.ui_theme_combo = QComboBox()
        self.ui_theme_combo.addItem("Dark (default)", "dark")
        self.ui_theme_combo.addItem("Light", "light")
        idx = self.ui_theme_combo.findData(self.config.ui_theme or "dark")
        if idx >= 0:
            self.ui_theme_combo.setCurrentIndex(idx)
        layout.addWidget(self.ui_theme_combo)
        layout.addWidget(_hint(
            "Dark uses the Subunit brand palette (deep navy + cyan). "
            "Light is for daylight desks."
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
        # Short-form / dictation styles
        self.cleanup_style_combo.addItem("Prompt — rewrite as structured AI prompt", "prompt")
        self.cleanup_style_combo.addItem("Email — polite, well-structured email body", "email")
        self.cleanup_style_combo.addItem("Slack — short casual chat message", "slack")
        self.cleanup_style_combo.addItem("Formal — business / executive tone", "formal")
        self.cleanup_style_combo.insertSeparator(self.cleanup_style_combo.count())
        # v0.6.0: long-form / meeting styles (read.ai-inspired).
        self.cleanup_style_combo.addItem("Summary — meeting / monologue → structured summary", "summary")
        self.cleanup_style_combo.addItem("Action Items — extract only action items as bullet list", "action_items")
        self.cleanup_style_combo.addItem("Minutes — formal meeting protocol (participants / decisions / actions)", "minutes")
        self.cleanup_style_combo.addItem("Decisions — extract only decisions made", "decisions")
        idx = self.cleanup_style_combo.findData(self.config.cleanup_style)
        if idx >= 0:
            self.cleanup_style_combo.setCurrentIndex(idx)
        layout.addWidget(self.cleanup_style_combo)

        # v0.3.25: Auto-Mode toggle. When on, the cleanup style above
        # acts as the FALLBACK; the actual style per-transcription is
        # derived from the active window via auto_mode.detect().
        self.row_auto_mode = _ToggleRow(
            "Auto-Mode — pick style by active window",
            "ChatGPT/Editor → Prompt · Mail apps → Email · Slack/Discord → Slack · "
            "Word/Docs → Formal. Falls back to your manual pick if no rule matches. "
            "Customise rules in the Auto-Mode tab.",
            self.config.cleanup_auto_mode,
        )
        layout.addWidget(self.row_auto_mode)

        # v0.6.0/v0.6.1: Long-form mode — for recordings longer than the
        # threshold, override the cleanup style.  v0.6.1 defaults to
        # "Raw" (no cleanup) so long captures stay as raw transcript;
        # the user can opt in to summary/action_items if they want.
        layout.addSpacing(8)
        layout.addWidget(_section_title("Long-form mode"))
        layout.addWidget(_hint(
            "Recordings longer than the threshold switch to a different "
            "cleanup style. Default: 'Raw' — keep the full transcript with "
            "no AI rewrite, so you don't lose content on a long dictation. "
            "Set threshold to 0 to disable the switch."
        ))

        lf_row = QHBoxLayout()
        lf_row.setContentsMargins(0, 0, 0, 0)
        lf_row.addWidget(QLabel("Activate when recording reaches"))
        self.long_form_threshold_spin = QSpinBox()
        self.long_form_threshold_spin.setRange(0, 3600)
        self.long_form_threshold_spin.setSuffix(" s")
        self.long_form_threshold_spin.setValue(
            int(getattr(self.config, "long_form_threshold_seconds", 240) or 0)
        )
        self.long_form_threshold_spin.setSpecialValueText("disabled")
        lf_row.addWidget(self.long_form_threshold_spin)
        lf_row.addSpacing(12)
        lf_row.addWidget(QLabel("→ apply"))
        self.long_form_style_combo = QComboBox()
        self.long_form_style_combo.addItem("Raw — keep full transcript, no cleanup", "raw")
        self.long_form_style_combo.addItem("Summary", "summary")
        self.long_form_style_combo.addItem("Action Items", "action_items")
        self.long_form_style_combo.addItem("Minutes (Protokoll)", "minutes")
        self.long_form_style_combo.addItem("Decisions", "decisions")
        lf_idx = self.long_form_style_combo.findData(
            getattr(self.config, "long_form_cleanup_style", "raw") or "raw"
        )
        if lf_idx >= 0:
            self.long_form_style_combo.setCurrentIndex(lf_idx)
        lf_row.addWidget(self.long_form_style_combo)
        lf_row.addStretch(1)
        layout.addLayout(lf_row)

        # v0.9.12: DACH Formatting Pack — post-process pass that fixes
        # German abbreviation spacing (z. B., d. h.), normalises currency
        # phrases (zweihundert Euro → 200 €), tightens punctuation
        # spacing, and turns ASCII straight quotes into curly German
        # „…". Off by default — opt in if you want it.
        layout.addSpacing(6)
        layout.addWidget(_section_title("DACH-Formatierung"))
        self.row_dach_format = _ToggleRow(
            "Deutsch / Österreich / Schweiz – Formatierungs-Pass",
            "Korrigiert Abkürzungen (z. B., d. h.), normalisiert Währung "
            "(zweihundert Euro → 200 €), tightent Satzzeichen-Spacing und "
            "ersetzt gerade Anführungszeichen durch deutsche („…“). Läuft "
            "lokal, nach dem Cleanup, vor dem Lexikon-Replace.",
            bool(getattr(self.config, "dach_format_enabled", False)),
        )
        layout.addWidget(self.row_dach_format)

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

    def _refresh_hotkey_warning(self, *_args) -> None:
        combo = self.hotkey_btn.value()
        reason = detect_hotkey_conflict(combo)
        if reason:
            self.hotkey_warning.setText(
                f"⚠ Likely conflict: {reason}. Pick a less-common combo or "
                "Sonar may not fire."
            )
            self.hotkey_warning.show()
        else:
            self.hotkey_warning.hide()

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
        # v0.9.15: the legacy Endpoint + API-Key inputs were removed.
        # Subunit auth runs entirely on the Bearer-token path now (sign in
        # via the Account tab). Endpoint stays in config — it's pinned to
        # the production server and the field added nothing but confusion
        # for users who already signed in with their account.
        panel = QWidget()
        panel.setObjectName("tabPage")
        f = QFormLayout(panel)
        f.setContentsMargins(0, 0, 0, 0)
        f.setSpacing(10)

        # Hidden — still tracked by Settings save/apply so a user who
        # downgrades doesn't lose a previously-entered key.
        self.subunit_endpoint_edit = QLineEdit(self.config.subunit_endpoint)
        self.subunit_endpoint_edit.setVisible(False)
        self.subunit_key_edit = QLineEdit(self.config.subunit_api_key)
        self.subunit_key_edit.setVisible(False)

        f.addRow("", _hint(
            "Premium DSGVO-konforme Transkription auf dem Subunit-Server. "
            "Anmelden im Tab \"Account\" — kein API-Key mehr nötig."
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
        # v0.9.12 Vocabulary v2: added "Aliases" + "Category" columns.
        # Aliases is a comma-separated cell for legibility; we split/join
        # on harvest. Category is a free-text cell (could become a combo
        # later, but the picker felt over-engineered for v0.9.12).
        self.vocab_table.setColumnCount(4)
        self.vocab_table.setHorizontalHeaderLabels(
            ["Sounds like", "Write as", "Aliases (Komma-getrennt)", "Kategorie"]
        )
        hdr = self.vocab_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
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
            aliases = entry.get("aliases") or []
            self.vocab_table.setItem(
                i, 2, QTableWidgetItem(", ".join(a for a in aliases if a))
            )
            self.vocab_table.setItem(
                i, 3, QTableWidgetItem(entry.get("category", "Other"))
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
        # v0.9.12: suggest new vocab from the on-device history (top
        # capitalized terms not yet in the Lexikon). Best-effort.
        suggest_btn = QPushButton("Aus Verlauf vorschlagen")
        suggest_btn.clicked.connect(self._suggest_vocab_from_history)
        btn_row.addWidget(suggest_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        return page

    def _remove_vocab_row(self) -> None:
        row = self.vocab_table.currentRow()
        if row >= 0:
            self.vocab_table.removeRow(row)

    def _suggest_vocab_from_history(self) -> None:
        """Scan the last N transcripts for capitalized multi-character
        tokens that aren't already in the Lexikon and aren't common
        German stopwords. Add the top 5 as draft rows."""
        import re
        from collections import Counter

        history = list(self.config.history or [])
        if not history:
            return
        existing_writes = {
            (self.vocab_table.item(r, 1).text() if self.vocab_table.item(r, 1) else "")
            .strip()
            .lower()
            for r in range(self.vocab_table.rowCount())
        }
        # Drop the most boring German sentence-starters.
        stop = {
            "Die", "Der", "Das", "Ein", "Eine", "Und", "Oder", "Aber",
            "Ich", "Du", "Wir", "Ihr", "Sie", "Es", "Mit", "Für",
            "Wenn", "Dann", "Also", "Ja", "Nein", "Was", "Wer", "Wo",
            "Wie", "Hallo", "Heute", "Morgen", "Gestern",
        }
        counter: Counter[str] = Counter()
        for entry in history:
            text = entry.get("text", "") or ""
            for tok in re.findall(r"\b[A-ZÄÖÜ][\wäöüß]{2,}\b", text):
                if tok in stop:
                    continue
                if tok.lower() in existing_writes:
                    continue
                counter[tok] += 1
        suggestions = [tok for tok, count in counter.most_common(5) if count >= 2]
        if not suggestions:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(
                self,
                "Lexikon",
                "Keine neuen Begriffe im Verlauf gefunden (mindestens 2× nötig).",
            )
            return
        # Append draft rows at the bottom of the table.
        for tok in suggestions:
            r = self.vocab_table.rowCount()
            self.vocab_table.insertRow(r)
            self.vocab_table.setItem(r, 0, QTableWidgetItem(tok))
            self.vocab_table.setItem(r, 1, QTableWidgetItem(tok))
            self.vocab_table.setItem(r, 2, QTableWidgetItem(""))
            self.vocab_table.setItem(r, 3, QTableWidgetItem("Other"))

    def _harvest_vocab(self) -> list[dict]:
        out: list[dict] = []
        for r in range(self.vocab_table.rowCount()):
            sl_item = self.vocab_table.item(r, 0)
            wa_item = self.vocab_table.item(r, 1)
            al_item = self.vocab_table.item(r, 2)
            cat_item = self.vocab_table.item(r, 3)
            sl = (sl_item.text().strip() if sl_item else "")
            wa = (wa_item.text().strip() if wa_item else "")
            al = (al_item.text().strip() if al_item else "")
            cat = (cat_item.text().strip() if cat_item else "")
            if sl and wa:
                aliases = [a.strip() for a in al.split(",") if a.strip()]
                out.append({
                    "sounds_like": sl,
                    "write_as": wa,
                    "aliases": aliases,
                    "category": cat or "Other",
                })
        return out

    # ── Tab: Auto-Mode (custom window→style rules) ─────────────────────────

    def _build_auto_mode_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(10)

        layout.addWidget(_section_title("Custom rules"))
        layout.addWidget(_hint(
            "When Auto-Mode is on (General tab), Sonar picks the "
            "cleanup style based on the active window. Add your own rules "
            "below — e.g. window contains \"Notion\" → Prompt. Custom rules "
            "override the built-in catalogue. Match is case-insensitive "
            "substring (not regex)."
        ))

        self.auto_table = QTableWidget()
        self.auto_table.setColumnCount(2)
        self.auto_table.setHorizontalHeaderLabels(["Window contains", "Cleanup style"])
        hdr = self.auto_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.auto_table.verticalHeader().setVisible(False)

        existing = list((self.config.auto_mode_overrides or {}).items())
        self.auto_table.setRowCount(max(6, len(existing) + 2))
        for i, (pattern, style) in enumerate(existing):
            self.auto_table.setItem(i, 0, QTableWidgetItem(pattern))
            self.auto_table.setCellWidget(i, 1, self._make_style_combo(style))
        # Pre-fill empty rows with a default combo so the user can pick a
        # style before typing the pattern (the harvest skips empty patterns).
        for i in range(len(existing), self.auto_table.rowCount()):
            self.auto_table.setCellWidget(i, 1, self._make_style_combo("prompt"))
        layout.addWidget(self.auto_table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_row = QPushButton("Add row")
        add_row.clicked.connect(self._add_auto_row)
        btn_row.addWidget(add_row)
        clear_row = QPushButton("Remove selected")
        clear_row.clicked.connect(self._remove_auto_row)
        btn_row.addWidget(clear_row)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        layout.addSpacing(6)
        layout.addWidget(_section_title("Built-in rules"))
        catalog = _auto_mode.catalog()
        # Group labels by style so the reference list reads compactly.
        by_style: dict[str, list[str]] = {}
        for style, label, _ in catalog:
            by_style.setdefault(style, []).append(label)
        style_titles = {
            "prompt": "Prompt", "email": "Email",
            "slack": "Slack", "formal": "Formal",
        }
        lines = []
        for style in ("prompt", "email", "slack", "formal"):
            apps = by_style.get(style, [])
            if apps:
                lines.append(f"<b>{style_titles[style]}</b> — {', '.join(apps)}")
        ref = QLabel("<br>".join(lines))
        ref.setObjectName("dim")
        ref.setWordWrap(True)
        ref.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(ref)

        return page

    def _make_style_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("Prompt", "prompt")
        combo.addItem("Email", "email")
        combo.addItem("Slack", "slack")
        combo.addItem("Formal", "formal")
        idx = combo.findData(current if current in {"prompt", "email", "slack", "formal"} else "prompt")
        if idx >= 0:
            combo.setCurrentIndex(idx)
        return combo

    def _add_auto_row(self) -> None:
        r = self.auto_table.rowCount()
        self.auto_table.setRowCount(r + 1)
        self.auto_table.setCellWidget(r, 1, self._make_style_combo("prompt"))

    def _remove_auto_row(self) -> None:
        row = self.auto_table.currentRow()
        if row >= 0:
            self.auto_table.removeRow(row)

    def _harvest_auto_overrides(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in range(self.auto_table.rowCount()):
            pat_item = self.auto_table.item(r, 0)
            pat = (pat_item.text().strip() if pat_item else "")
            if not pat:
                continue
            combo = self.auto_table.cellWidget(r, 1)
            style = combo.currentData() if isinstance(combo, QComboBox) else "prompt"
            if style in {"prompt", "email", "slack", "formal"}:
                out[pat] = style
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
        layout.addWidget(_section_title("Style"))
        self.orb_style_combo = QComboBox()
        # v0.9.6 / v0.9.7: 5 new styles up top (TJ-pick list), legacy below.
        self.orb_style_combo.addItem("Sonar Ping — mic-reactive expanding rings ⭐", "ping")
        self.orb_style_combo.addItem("Status Pill — compact label + state dot", "pill")
        self.orb_style_combo.addItem("Constellation — orbiting nodes (rotating)", "constellation")
        self.orb_style_combo.addItem("Bars — vertical audio equalizer", "bars")
        self.orb_style_combo.addItem("Pulse Wave — horizontal sine, mic-reactive", "pulse-wave")
        self.orb_style_combo.addItem("Sphere — glass dot (legacy default)", "sphere")
        self.orb_style_combo.addItem("Sonar — animated logo (rings + bars, legacy)", "sonar")
        self.orb_style_combo.addItem("Wave — horizontal sine", "wave")
        self.orb_style_combo.addItem("Classic — minimal dot", "classic")
        idx = self.orb_style_combo.findData(
            getattr(self.config, "orb_overlay_style", "sphere") or "sphere"
        )
        if idx >= 0:
            self.orb_style_combo.setCurrentIndex(idx)
        layout.addWidget(self.orb_style_combo)
        layout.addWidget(_hint(
            "Sonar reacts to your microphone — concentric rings ping outward "
            "and the five center bars rise with your voice. Bars and Wave are "
            "more graph-like; Classic is a single dim dot."
        ))

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
        layout.addWidget(_section_title("Size"))
        self.orb_size_combo = QComboBox()
        # v0.9.16: tablet users (Erik on Win-ARM Surface) wanted the orb
        # bigger because the 1.0x default is too easy to lose on hi-DPI
        # screens. Picker not slider — fewer choices keeps the UX tight.
        self.orb_size_combo.addItem("Small (1.0×, default)", 1.0)
        self.orb_size_combo.addItem("Medium (1.5×)", 1.5)
        self.orb_size_combo.addItem("Large (2.0×)", 2.0)
        self.orb_size_combo.addItem("Tablet (2.5×)", 2.5)
        self.orb_size_combo.addItem("XL (3.0×)", 3.0)
        cur_size = float(getattr(self.config, "orb_overlay_size", 1.0) or 1.0)
        # Pick the closest configured size so an old value lands on a sensible
        # option even if it doesn't match exactly.
        best_idx = 0
        best_diff = abs(self.orb_size_combo.itemData(0) - cur_size)
        for i in range(1, self.orb_size_combo.count()):
            d = abs(self.orb_size_combo.itemData(i) - cur_size)
            if d < best_diff:
                best_diff, best_idx = d, i
        self.orb_size_combo.setCurrentIndex(best_idx)
        layout.addWidget(self.orb_size_combo)

        layout.addSpacing(6)
        layout.addWidget(_section_title("Idle behaviour"))
        # v0.9.17 (TJ): auto-hide the overlay completely while idle —
        # most users only want to see it during actual dictations.
        self.row_orb_auto_hide = _ToggleRow(
            "Nur bei Aufnahme anzeigen",
            "Versteckt das Overlay komplett wenn keine Aufnahme läuft. "
            "Erscheint nur kurz beim Hotkey-Druck und verschwindet wieder "
            "nach dem Transkriptionsergebnis.",
            getattr(self.config, "orb_overlay_auto_hide", False),
        )
        layout.addWidget(self.row_orb_auto_hide)

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

        # ── New (v0.9.5): browser-based Subunit-Account login ────────────
        layout.addWidget(_section_title("Subunit-Account (empfohlen)"))

        self.subunit_login_status_lbl = QLabel("")
        self.subunit_login_status_lbl.setWordWrap(True)
        layout.addWidget(self.subunit_login_status_lbl)

        login_row = QHBoxLayout()
        login_row.setSpacing(10)
        self.subunit_login_btn = QPushButton("Mit Subunit-Account anmelden")
        self.subunit_login_btn.setObjectName("primary")
        self.subunit_login_btn.clicked.connect(self._on_subunit_login)
        login_row.addWidget(self.subunit_login_btn)

        self.subunit_logout_btn = QPushButton("Abmelden")
        self.subunit_logout_btn.clicked.connect(self._on_subunit_logout)
        login_row.addWidget(self.subunit_logout_btn)
        login_row.addStretch(1)
        layout.addLayout(login_row)

        layout.addSpacing(6)
        layout.addWidget(_hint(
            "Öffnet auth.subunit.ai im Browser — du loggst dich dort mit "
            "deinem Subunit-Konto ein (oder erstellst eines, optional via "
            "Google), Sonar bekommt danach automatisch einen Token. Dein "
            "Passwort bleibt im Browser.\n\n"
            "DSGVO-konform, EU-gehostet."
        ))

        # 2026-05-16 (v0.9.7, TJ-feedback): inline Profile card showing
        # plan + access + workspace. Fetched live from /v1/account/info
        # via the Subunit Bearer token. Surfaces the answer to "habe ich
        # Pro oder nicht?" without TJ having to ping the admin panel.
        layout.addSpacing(14)
        self.profile_card = QFrame()
        self.profile_card.setObjectName("card")
        self.profile_card.setStyleSheet(
            f"QFrame#card {{ background: {NIGHT_2}; "
            f"border: 1px solid {NIGHT_BORDER}; border-radius: 10px; }}"
        )
        pc = QVBoxLayout(self.profile_card)
        pc.setContentsMargins(16, 14, 16, 14)
        pc.setSpacing(8)

        row_email = QHBoxLayout()
        row_email.setSpacing(8)
        lbl_email_key = QLabel("Email")
        lbl_email_key.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 0.1em;")
        row_email.addWidget(lbl_email_key)
        row_email.addStretch(1)
        self.profile_email_val = QLabel("—")
        self.profile_email_val.setStyleSheet(f"color: {WHITE}; font-weight: 600;")
        row_email.addWidget(self.profile_email_val)
        pc.addLayout(row_email)

        row_plan = QHBoxLayout()
        row_plan.setSpacing(8)
        lbl_plan_key = QLabel("Plan")
        lbl_plan_key.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 0.1em;")
        row_plan.addWidget(lbl_plan_key)
        row_plan.addStretch(1)
        self.profile_plan_badge = QLabel("—")
        self.profile_plan_badge.setStyleSheet(
            "color: #050b1a; background: #475569; font-weight: 700; "
            "padding: 2px 10px; border-radius: 999px; font-size: 11px; letter-spacing: 0.1em;"
        )
        row_plan.addWidget(self.profile_plan_badge)
        pc.addLayout(row_plan)

        row_access = QHBoxLayout()
        row_access.setSpacing(8)
        lbl_access_key = QLabel("Cloud-Transkription")
        lbl_access_key.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 0.1em;")
        row_access.addWidget(lbl_access_key)
        row_access.addStretch(1)
        self.profile_access_val = QLabel("—")
        self.profile_access_val.setStyleSheet(f"color: {WHITE_DIM};")
        row_access.addWidget(self.profile_access_val)
        pc.addLayout(row_access)

        layout.addWidget(self.profile_card)

        # 2026-05-16 (v0.9.10, Codex self-heal): Refresh + Diagnose buttons
        # under the Profile card so Erik-style debug sessions are a click
        # away — no need to ping TJ for "what tier am I on?".
        diag_row = QHBoxLayout()
        diag_row.setSpacing(8)
        self.profile_refresh_btn = QPushButton("Account aktualisieren")
        self.profile_refresh_btn.clicked.connect(self._refresh_profile_card)
        diag_row.addWidget(self.profile_refresh_btn)
        self.profile_diag_btn = QPushButton("Diagnose kopieren")
        self.profile_diag_btn.clicked.connect(self._copy_diagnostics)
        diag_row.addWidget(self.profile_diag_btn)
        diag_row.addStretch(1)
        layout.addLayout(diag_row)

        # Update the status line + button states from current config.
        self._refresh_subunit_login_status()
        # Kick off the live profile fetch in the background — the
        # network round-trip shouldn't block opening Settings.
        self._refresh_profile_card()

        # v0.3.29 — Subunit Suite: Voice → Synapse Knowledge Base bridge
        layout.addSpacing(12)
        layout.addWidget(_section_title("Subunit Suite"))
        self.row_synapse_save = _ToggleRow(
            "Save transcripts to Synapse Knowledge Base",
            "Every transcript is sent to your private Synapse collection right "
            "after cleanup. Turns Voice dictations into long-term, semantically "
            "searchable memory across the Subunit suite. Requires Pro / trial; "
            "your Subunit API key authenticates the call.",
            self.config.synapse_save_enabled,
        )
        layout.addWidget(self.row_synapse_save)

        # 2026-05-14 (v0.8.0, codex top 1): speaker diarization.
        self.row_diarization = _ToggleRow(
            "Speaker-Erkennung (Cloud)",
            "Erkenne automatisch wer im Meeting wann gesprochen hat. "
            "Läuft auf transcribe.subunit.ai (Hamburg) — selbe DSGVO-Surface "
            "wie Cloud-Transkription. Aktiviert sich automatisch für "
            "Meetings ≥ 4 Minuten. Erfordert gültigen Subunit API-Key.",
            self.config.diarization_enabled,
        )
        layout.addWidget(self.row_diarization)

        # v0.9.11: Privatsphäre — opt-out of the on-device transcript
        # history. Counters stay on (so the totals shown in this dialog
        # remain truthful), but no per-snippet text is stored. Existing
        # entries are kept until the user clears them manually.
        self.row_history_enabled = _ToggleRow(
            "Verlauf speichern (Recent-Transcripts)",
            "Wenn aus: keine Transkripte werden auf diesem Gerät gespeichert. "
            "Du verlierst die Re-paste-Funktion und die Liste auf dem Hauptscreen, "
            "aber Aufnahmen verlassen den RAM nie. Bestehende Einträge bleiben "
            "bis du sie über History → Clear löschst.",
            bool(getattr(self.config, "history_enabled", True)),
        )
        layout.addWidget(self.row_history_enabled)

        # 2026-05-14 (codex review #5): make DSGVO concrete and visible in
        # the product instead of leaving it as a brand claim. EU buyers
        # ask for these facts every time — surface them so they don't
        # have to email Erik to find out.
        layout.addSpacing(12)
        layout.addWidget(_section_title("Datenschutz / DSGVO"))

        privacy_box = QFrame()
        privacy_box.setStyleSheet(
            f"QFrame {{ background: {NIGHT_2}; border: 1px solid {NIGHT_BORDER}; "
            f"border-radius: 8px; padding: 12px; }}"
        )
        pl = QVBoxLayout(privacy_box)
        pl.setSpacing(6)
        pl.setContentsMargins(12, 10, 12, 12)

        local_only = (self.config.mode or "").lower() == "local"
        mode_line = (
            "Verarbeitung: <b>nur lokal</b> auf diesem Gerät (keine Cloud-Übertragung)."
            if local_only
            else "Verarbeitung: <b>Cloud-Modus</b> – Audio + Transkripte werden zur "
                 "Subunit-API in Hamburg gesendet."
        )
        cloud_url = (self.config.subunit_endpoint or "").strip() or "https://transcribe.subunit.ai"
        rows = [
            f"<b>Speicherort der Server:</b> Hamburg, Deutschland (EU-only)",
            mode_line,
            f"<b>Aufbewahrung:</b> Lokal unter <code>~/.config/synapse-voice</code> "
            f"– du löschst jederzeit per <i>Delete</i> in <i>Meetings</i> / <i>History</i>.",
            "<b>Audio-Dateien:</b> nach der Transkription verworfen (nichts wird gespeichert).",
            "<b>Cleanup-Modell:</b> Claude Haiku via OpenRouter (EU-Region), Temperature 0, "
            "keine Trainingsnutzung deiner Daten.",
            f"<b>Cloud-Endpoint:</b> <code>{cloud_url}</code>",
        ]
        for html in rows:
            lbl = QLabel(html)
            lbl.setWordWrap(True)
            lbl.setTextFormat(Qt.TextFormat.RichText)
            lbl.setStyleSheet(f"color: {WHITE}; font-size: 12px;")
            pl.addWidget(lbl)

        dsgvo_btn_row = QHBoxLayout()
        dsgvo_btn_row.setSpacing(8)
        self.btn_open_config_dir = QPushButton("Datenordner öffnen…")
        self.btn_open_config_dir.clicked.connect(self._on_open_config_dir)
        self.btn_avv_download = QPushButton("AVV / DPA anfordern")
        self.btn_avv_download.clicked.connect(self._on_request_avv)
        self.btn_delete_all_meetings = QPushButton("Alle Meetings löschen")
        self.btn_delete_all_meetings.setObjectName("danger")
        self.btn_delete_all_meetings.clicked.connect(self._on_delete_all_meetings)
        dsgvo_btn_row.addWidget(self.btn_open_config_dir)
        dsgvo_btn_row.addWidget(self.btn_avv_download)
        dsgvo_btn_row.addStretch(1)
        dsgvo_btn_row.addWidget(self.btn_delete_all_meetings)
        pl.addLayout(dsgvo_btn_row)

        layout.addWidget(privacy_box)

        layout.addStretch(1)
        return page

    def _on_open_config_dir(self) -> None:
        """Reveal the user's Sonar config directory in the OS file explorer."""
        from pathlib import Path
        config_dir = Path.home() / ".config" / "synapse-voice"
        config_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir)))

    def _on_request_avv(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        # AVV/DPA workflow — we don't have a self-serve PDF yet, so the
        # button mailto-launches a request that the agency answers within
        # 1 working day. Better than buyers having to figure out who to
        # contact.
        email = self.config.account_email or ""
        subject = "AVV / DPA für Sonar"
        body = (
            "Hallo Subunit-Team,%0D%0A%0D%0A"
            "ich möchte einen AVV / DPA für die Nutzung von Sonar.%0D%0A%0D%0A"
            f"Account: {email}%0D%0A"
            "Firma: %0D%0A"
            "Anschrift: %0D%0A%0D%0A"
            "Vielen Dank!"
        )
        url = f"mailto:hello@subunit.ai?subject={subject}&body={body}"
        QDesktopServices.openUrl(QUrl(url))
        QMessageBox.information(
            self,
            "AVV / DPA",
            "Eine Mail an hello@subunit.ai ist vorbereitet. "
            "Wir antworten innerhalb eines Werktags mit dem unterzeichneten AVV.",
        )

    def _on_delete_all_meetings(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        confirm = QMessageBox.question(
            self,
            "Alle Meetings löschen?",
            "Das löscht ALLE auf diesem Gerät gespeicherten Meetings unwiderruflich.\n\n"
            "Cloud-/Bridge-Kopien sind davon nicht betroffen.\n\nWeiter?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        try:
            from ..meetings import MeetingsStore
            store = MeetingsStore()
            count = 0
            for m in store.list_all():
                store.delete(m.id)
                count += 1
            QMessageBox.information(self, "Gelöscht", f"{count} Meetings entfernt.")
        except Exception as e:
            QMessageBox.warning(self, "Fehler", f"Löschen fehlgeschlagen:\n{e}")

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
            QMessageBox.warning(self, "Sonar", "Please enter a valid email.")
            return
        endpoint = (
            self.subunit_endpoint_edit.text().strip()
            or self.config.subunit_endpoint
        )
        try:
            acct = _account_api.sign_up(endpoint, email)
        except Exception as e:
            QMessageBox.critical(self, "Sonar — sign-up failed", str(e))
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
        QMessageBox.information(self, "Sonar", msg)

    def _on_account_logout(self) -> None:
        self.config.account_email = ""
        self.config.subunit_api_key = ""
        self.config.save()
        self.account_email_edit.setText("")
        self.subunit_key_edit.setText("")
        self._refresh_account_status()

    # ── Subunit-Account browser-login flow (v0.9.5) ─────────────────────

    def _copy_diagnostics(self) -> None:
        """Assemble a diagnostics text payload + copy to the system
        clipboard. Includes version, OS/arch, mode, account-tier, last
        log lines — no secrets (tokens, API keys are redacted)."""
        import platform
        import sys
        from .. import __version__
        from PyQt6.QtWidgets import QApplication

        def redact(v: str) -> str:
            if not v:
                return ""
            if len(v) <= 8:
                return "***"
            return v[:4] + "…" + v[-4:]

        lines = []
        lines.append("=== Sonar Diagnose ===")
        lines.append(f"Version: v{__version__}")
        lines.append(f"OS: {platform.system()} {platform.release()} {platform.machine()}")
        lines.append(f"Python: {sys.version.split()[0]}")
        lines.append("")
        lines.append("--- Config (redacted) ---")
        lines.append(f"Mode: {self.config.mode}")
        lines.append(f"Local model: {self.config.local_model}")
        lines.append(f"Hotkey: {self.config.hotkey}")
        lines.append(f"Recording mode: {self.config.recording_mode}")
        lines.append(f"Subunit endpoint: {self.config.subunit_endpoint}")
        lines.append(f"Subunit Bearer: {redact(getattr(self.config, 'subunit_access_token', '') or '')}")
        lines.append(f"Subunit API-Key: {redact(getattr(self.config, 'subunit_api_key', '') or '')}")
        lines.append(f"Account email: {getattr(self.config, 'account_email', '') or '—'}")
        lines.append(f"Cloud quality mode: {getattr(self.config, 'cloud_quality_mode', 'auto')}")
        lines.append(f"Overlay style: {getattr(self.config, 'orb_overlay_style', 'sphere')}")
        lines.append("")
        lines.append("--- Profile (live) ---")
        lines.append(f"Email displayed: {self.profile_email_val.text()}")
        lines.append(f"Plan badge: {self.profile_plan_badge.text()}")
        lines.append(f"Access: {self.profile_access_val.text()}")
        # Tail of the log file if accessible. v0.9.13 (Codex P1): each
        # log line is redacted before it's pasted, since log files often
        # contain transcribed text, request headers, URLs with tokens,
        # and auth error bodies. The diagnostic dump is explicitly meant
        # to be shareable with support — leaking those would defeat the
        # whole "redacted config" block above it.
        try:
            from ..logger import log_file_path
            p = log_file_path()
            if p and p.exists():
                lines.append("")
                lines.append(f"--- Log tail ({p.name}, last 30 lines, redacted) ---")
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    tail = f.readlines()[-30:]
                lines.extend(_redact_log_line(t.rstrip()) for t in tail)
        except Exception as exc:
            lines.append(f"(log tail unavailable: {exc})")

        text = "\n".join(lines)
        try:
            QApplication.clipboard().setText(text)
            self.profile_diag_btn.setText("✓ In Zwischenablage")
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(2200, lambda: self.profile_diag_btn.setText("Diagnose kopieren"))
        except Exception:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Sonar — Diagnose", text)

    def _refresh_profile_card(self) -> None:
        """Populate the inline Profile card with live data from the
        transcribe-server /v1/account/info endpoint. Runs the network
        call on a background thread so Settings stays responsive even
        if the server is slow / offline."""
        import threading
        from PyQt6.QtCore import QTimer

        # Default while we wait / on failure
        access = (getattr(self.config, "subunit_access_token", "") or "").strip()
        api_key = (getattr(self.config, "subunit_api_key", "") or "").strip()
        if not access and not api_key:
            self.profile_email_val.setText("Nicht angemeldet")
            self.profile_plan_badge.setText("—")
            self.profile_plan_badge.setStyleSheet(
                "color: #050b1a; background: #475569; font-weight: 700; "
                "padding: 2px 10px; border-radius: 999px; font-size: 11px; letter-spacing: 0.1em;"
            )
            self.profile_access_val.setText("—")
            return

        self.profile_email_val.setText("Wird geladen …")
        self.profile_access_val.setText("…")

        result_box: list = []

        def fetch() -> None:
            import urllib.error, urllib.request, json as _json
            endpoint = (getattr(self.config, "subunit_endpoint", "") or "https://transcribe.subunit.ai").rstrip("/")
            url = endpoint + "/v1/account/info"
            headers = {}
            if access:
                headers["Authorization"] = "Bearer " + access
            elif api_key:
                headers["X-API-Key"] = api_key
            try:
                req = urllib.request.Request(url, method="GET", headers=headers)
                with urllib.request.urlopen(req, timeout=8) as resp:
                    result_box.append(_json.loads(resp.read().decode("utf-8")))
            except urllib.error.HTTPError as exc:
                try:
                    body = _json.loads(exc.read().decode("utf-8"))
                except Exception:
                    body = {"detail": str(exc)}
                result_box.append({"_error": True, "status": exc.code, "body": body})
            except Exception as exc:
                result_box.append({"_error": True, "message": str(exc)})

        threading.Thread(target=fetch, name="sonar-profile-fetch", daemon=True).start()

        def check() -> None:
            if not result_box:
                QTimer.singleShot(250, check)
                return
            r = result_box[0]
            if r.get("_error"):
                self.profile_email_val.setText("Fehler beim Laden")
                self.profile_plan_badge.setText("?")
                self.profile_access_val.setText("Server nicht erreichbar")
                return
            email = (r.get("email") or "").strip() or "—"
            plan = (r.get("plan") or "free").lower()
            has_access = bool(r.get("has_access"))
            self.profile_email_val.setText(email)
            # Plan badge styling per tier
            plan_styles = {
                "operator":   ("OPERATOR",   "#c084fc"),
                "ops":        ("OPS",        "#c084fc"),
                "enterprise": ("ENTERPRISE", "#a855f7"),
                "pro":        ("PRO",        "#06b6d4"),
                "pilot":      ("PILOT",      "#f59e0b"),
                "basic":      ("BASIC",      "#64748b"),
                "trial":      ("TRIAL",      "#f59e0b"),
                "free":       ("FREE",       "#475569"),
            }
            label, bg = plan_styles.get(plan, (plan.upper(), "#475569"))
            self.profile_plan_badge.setText(label)
            self.profile_plan_badge.setStyleSheet(
                f"color: #050b1a; background: {bg}; font-weight: 700; "
                "padding: 2px 10px; border-radius: 999px; font-size: 11px; letter-spacing: 0.1em;"
            )
            if has_access:
                self.profile_access_val.setText("✓ Aktiv")
                self.profile_access_val.setStyleSheet("color: #10b981;")
            else:
                self.profile_access_val.setText("✗ Upgrade nötig")
                self.profile_access_val.setStyleSheet("color: #ef4444;")

        QTimer.singleShot(250, check)

    def _refresh_subunit_login_status(self) -> None:
        """Sync the status label + button states with the current config."""
        access = (self.config.subunit_access_token or "").strip()
        if access:
            email = (self.config.account_email or "—").strip() or "—"
            self.subunit_login_status_lbl.setText(
                f"✓ Angemeldet als <b>{email}</b>"
            )
            self.subunit_login_status_lbl.setTextFormat(Qt.TextFormat.RichText)
            self.subunit_login_btn.setText("Erneut anmelden")
            self.subunit_logout_btn.setEnabled(True)
        else:
            self.subunit_login_status_lbl.setText(
                "Noch nicht angemeldet."
            )
            self.subunit_login_btn.setText("Mit Subunit-Account anmelden")
            self.subunit_logout_btn.setEnabled(False)
        self._refresh_profile_card()

    def _on_subunit_login(self) -> None:
        """Kick off the browser-based login flow on a background thread.

        Blocking the Qt event loop while we wait 5 minutes for a callback
        would freeze the entire app; thread it and post the result back
        via QTimer.singleShot from a Python thread.
        """
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QMessageBox
        import threading

        from ..subunit_auth import login_interactive, fetch_me

        self.subunit_login_btn.setEnabled(False)
        self.subunit_login_btn.setText("Browser öffnet sich …")
        self.subunit_login_status_lbl.setText(
            "Im Browser anmelden — diese Settings bleiben offen, "
            "warten auf den Login …"
        )

        result_box: list = []

        def run() -> None:
            try:
                tokens = login_interactive()
                if tokens is None:
                    result_box.append({"error": "Timeout oder abgebrochen."})
                    return
                me = fetch_me(tokens.access_token) or {}
                result_box.append({
                    "tokens": tokens,
                    "email": me.get("email") or me.get("user", {}).get("email", ""),
                })
            except Exception as exc:  # noqa: BLE001
                result_box.append({"error": str(exc)})

        threading.Thread(target=run, name="subunit-login-ui", daemon=True).start()

        # Poll the result_box on the Qt thread every 250 ms (cheap).
        def check() -> None:
            if not result_box:
                QTimer.singleShot(250, check)
                return
            result = result_box[0]
            if "error" in result:
                self._refresh_subunit_login_status()
                self.subunit_login_btn.setEnabled(True)
                QMessageBox.warning(
                    self, "Sonar — Login",
                    f"Anmeldung fehlgeschlagen: {result['error']}",
                )
                return
            tokens = result["tokens"]
            email = (result.get("email") or "").strip()

            self.config.subunit_access_token = tokens.access_token
            self.config.subunit_refresh_token = tokens.refresh_token
            self.config.subunit_token_issued_at = tokens.issued_at
            self.config.subunit_token_expires_in = tokens.expires_in
            self.config.subunit_workspace_id = tokens.workspace_id or ""
            if email:
                self.config.account_email = email
            self.config.save()

            self.subunit_login_btn.setEnabled(True)
            self._refresh_subunit_login_status()
            self._refresh_profile_card()
            QMessageBox.information(
                self, "Sonar — Login",
                "Erfolgreich angemeldet. Cloud-Transkription nutzt jetzt deinen Subunit-Account.",
            )

        QTimer.singleShot(250, check)

    def _on_subunit_logout(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        if QMessageBox.question(
            self, "Sonar — Abmelden",
            "Subunit-Account von Sonar abmelden? Die Cloud-Transkription "
            "wechselt dann zurück zum API-Key (falls vorhanden) oder zu Lokal.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.config.subunit_access_token = ""
        self.config.subunit_refresh_token = ""
        self.config.subunit_token_issued_at = 0.0
        self.config.subunit_token_expires_in = 0
        self.config.subunit_workspace_id = ""
        self.config.save()
        self._refresh_subunit_login_status()

    # ── Tab 3: About ───────────────────────────────────────────────────────

    def _build_about_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("tabPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 18, 2, 18)
        layout.setSpacing(14)

        layout.addWidget(_section_title("About"))
        layout.addWidget(QLabel(f"Sonar  v{__version__}"))
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
        config.orb_overlay_style = (
            self.orb_style_combo.currentData() if hasattr(self, "orb_style_combo") else "sphere"
        ) or "sphere"
        config.orb_position = self.orb_position_combo.currentData() or "bottom-center"
        if hasattr(self, "orb_size_combo"):
            try:
                config.orb_overlay_size = float(self.orb_size_combo.currentData() or 1.0)
            except (TypeError, ValueError):
                config.orb_overlay_size = 1.0
        if hasattr(self, "row_orb_auto_hide"):
            config.orb_overlay_auto_hide = self.row_orb_auto_hide.is_on()
        config.orb_idle_pulse = self.row_orb_pulse.is_on()
        config.recording_mode = self.recording_mode_combo.currentData() or "toggle"
        config.mic_device_name = self.mic_combo.currentData() or ""
        new_ui_lang = self.ui_language_combo.currentData() or "de"
        if new_ui_lang != config.ui_language:
            config.ui_language = new_ui_lang
            from .. import i18n
            i18n.set_language(new_ui_lang)
        new_ui_theme = self.ui_theme_combo.currentData() or "dark"
        if new_ui_theme != config.ui_theme:
            config.ui_theme = new_ui_theme
            try:
                from PyQt6.QtWidgets import QApplication
                from .. import theme as _theme
                _theme.apply(QApplication.instance(), new_ui_theme)
            except Exception:
                pass
        # v0.3.9 Lexikon: harvest vocab table into list[dict]
        if hasattr(self, "vocab_table"):
            config.vocabulary = self._harvest_vocab()
        config.cleanup_enabled = self.row_cleanup.is_on()
        config.cleanup_style = self.cleanup_style_combo.currentData() or "prompt"
        if hasattr(self, "row_auto_mode"):
            config.cleanup_auto_mode = self.row_auto_mode.is_on()
        if hasattr(self, "long_form_threshold_spin"):
            config.long_form_threshold_seconds = int(self.long_form_threshold_spin.value())
        if hasattr(self, "long_form_style_combo"):
            config.long_form_cleanup_style = (
                self.long_form_style_combo.currentData() or "raw"
            )
        if hasattr(self, "auto_table"):
            config.auto_mode_overrides = self._harvest_auto_overrides()
        if hasattr(self, "row_synapse_save"):
            config.synapse_save_enabled = self.row_synapse_save.is_on()
        if hasattr(self, "row_diarization"):
            config.diarization_enabled = self.row_diarization.is_on()
        if hasattr(self, "row_history_enabled"):
            config.history_enabled = self.row_history_enabled.is_on()
        if hasattr(self, "row_dach_format"):
            config.dach_format_enabled = self.row_dach_format.is_on()
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
