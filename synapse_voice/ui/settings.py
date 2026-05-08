"""Settings dialog — Phase 2 polish."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QVBoxLayout,
)

from .. import autostart
from ..config import Config
from .hotkey_capture import HotkeyCaptureButton

DARK_QSS = """
QDialog { background: #020817; color: #e6f2fb; }
QLabel { color: #c8d6df; }
QLineEdit, QComboBox, QPushButton {
    background: #0c1828; color: white; border: 1px solid #1f3145;
    border-radius: 6px; padding: 6px 8px; min-width: 280px;
}
QLineEdit:focus, QComboBox:focus, QPushButton:focus { border-color: #40d6ff; }
QPushButton { text-align: left; }
QPushButton:hover { border-color: #40d6ff; }
QCheckBox { color: #c8d6df; padding: 2px 0; }
QDialogButtonBox QPushButton {
    background: #0c1828; color: white; border: 1px solid #1f3145;
    border-radius: 6px; padding: 6px 14px; min-width: 80px; text-align: center;
}
QDialogButtonBox QPushButton:hover { border-color: #40d6ff; }
QDialogButtonBox QPushButton:default {
    background: #40d6ff; color: #020817; border: none;
}
"""


class SettingsDialog(QDialog):
    def __init__(self, config: Config, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Synapse Voice — Settings")
        self.setStyleSheet(DARK_QSS)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.config = config

        form = QFormLayout()
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(12)

        self.hotkey_btn = HotkeyCaptureButton(config.hotkey)
        form.addRow("Hotkey", self.hotkey_btn)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Local (faster-whisper)", "local")
        self.mode_combo.addItem("Cloud — OpenRouter", "openrouter")
        self.mode_combo.addItem("Cloud — Subunit (DSGVO)", "subunit")
        idx = self.mode_combo.findData(config.mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        form.addRow("Mode", self.mode_combo)

        self.model_combo = QComboBox()
        for m in ["base", "small", "medium", "large-v3"]:
            self.model_combo.addItem(m, m)
        idx = self.model_combo.findData(config.local_model)
        if idx >= 0:
            self.model_combo.setCurrentIndex(idx)
        form.addRow("Local model", self.model_combo)

        self.lang_edit = QLineEdit(config.language)
        form.addRow("Language", self.lang_edit)

        self.openrouter_edit = QLineEdit(config.openrouter_api_key)
        self.openrouter_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.openrouter_edit.setPlaceholderText("sk-or-...")
        form.addRow("OpenRouter API key", self.openrouter_edit)

        self.subunit_edit = QLineEdit(config.subunit_endpoint)
        form.addRow("Subunit endpoint", self.subunit_edit)

        self.subunit_key_edit = QLineEdit(config.subunit_api_key)
        self.subunit_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.subunit_key_edit.setPlaceholderText("optional X-API-Key")
        form.addRow("Subunit API key", self.subunit_key_edit)

        self.autopaste_cb = QCheckBox("Auto-paste into target window")
        self.autopaste_cb.setChecked(config.autopaste)
        form.addRow("", self.autopaste_cb)

        self.target_lock_cb = QCheckBox("Lock target window at hotkey-press")
        self.target_lock_cb.setChecked(config.target_lock)
        form.addRow("", self.target_lock_cb)

        self.show_bubble_cb = QCheckBox("Show floating bubble near cursor")
        self.show_bubble_cb.setChecked(config.show_bubble)
        form.addRow("", self.show_bubble_cb)

        self.autostart_cb = QCheckBox("Start automatically with system")
        self.autostart_cb.setChecked(autostart.is_enabled())
        form.addRow("", self.autostart_cb)

        wrap = QVBoxLayout(self)
        wrap.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        wrap.addWidget(buttons)

    def apply_to(self, config: Config) -> None:
        config.hotkey = self.hotkey_btn.value() or "<ctrl>+<shift>+<space>"
        config.mode = self.mode_combo.currentData() or "local"
        config.local_model = self.model_combo.currentData() or "base"
        config.language = self.lang_edit.text().strip() or "de"
        config.openrouter_api_key = self.openrouter_edit.text().strip()
        config.subunit_endpoint = (
            self.subunit_edit.text().strip()
            or "https://transcribe.subunit.ai/v1/transcribe"
        )
        config.subunit_api_key = self.subunit_key_edit.text().strip()
        config.autopaste = self.autopaste_cb.isChecked()
        config.target_lock = self.target_lock_cb.isChecked()
        config.show_bubble = self.show_bubble_cb.isChecked()
        config.save()

        if self.autostart_cb.isChecked():
            autostart.enable()
        else:
            autostart.disable()
