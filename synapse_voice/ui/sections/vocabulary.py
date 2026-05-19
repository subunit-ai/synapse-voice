"""v0.10.0 Hub: Vocabulary section.

There's no dedicated VocabularyDialog — the table lives inside the
SettingsDialog's Vocabulary tab. Same pattern as SettingsSection:
build a hidden host dialog, lift the vocabulary panel by label, and
re-parent it into our own layout. Save delegates back to the dialog's
apply_to so vocabulary edits land in the same place they always did."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import Config


CYAN = "#06b6d4"
WHITE = "#e2e8f0"
WHITE_DIM = "#94a3b8"


class VocabularySection(QWidget):
    def __init__(
        self,
        config: Config,
        on_apply: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self._on_apply = on_apply or (lambda: None)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        h = QLabel("Vocabulary")
        h.setStyleSheet(f"color: {WHITE}; font-size: 22px; font-weight: 700;")
        outer.addWidget(h)
        sub = QLabel(
            "Eigene Begriffe biasen Whisper zur korrekten Schreibweise. "
            "v0.9.17 hat eine Basis-Liste vorgeseedet — du kannst frei "
            "erweitern oder Einträge löschen."
        )
        sub.setStyleSheet(f"color: {WHITE_DIM}; font-size: 13px;")
        sub.setWordWrap(True)
        outer.addWidget(sub)

        try:
            from ..settings import SettingsDialog
            self._dialog_host = SettingsDialog(config=config)
            self._dialog_host.setVisible(False)
            panel = self._lift_panel_by_label("Vocabulary")
            if panel is None:
                raise RuntimeError("Vocabulary panel not found in SettingsDialog")
            outer.addWidget(panel, 1)
        except Exception as exc:
            err = QLabel(f"Vocabulary konnte nicht geladen werden: {exc}")
            err.setStyleSheet(f"color: {WHITE_DIM}; padding: 24px;")
            err.setWordWrap(True)
            outer.addWidget(err, 1)
            return

        # Save bar
        save_row = QHBoxLayout()
        save_row.addStretch()
        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color: {CYAN}; font-size: 12px;")
        save_row.addWidget(self._status_lbl)
        save_btn = QPushButton("Speichern")
        save_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        save_btn.setMinimumHeight(40)
        save_btn.setMinimumWidth(140)
        save_btn.setStyleSheet(
            f"QPushButton {{ background: {CYAN}; color: #031426; border: none; border-radius: 8px;"
            f" padding: 8px 22px; font-weight: 700; }}"
            f"QPushButton:hover {{ background: #22d3ee; }}"
        )
        save_btn.clicked.connect(self._save)
        save_row.addWidget(save_btn)
        outer.addLayout(save_row)

    def _lift_panel_by_label(self, label: str) -> Optional[QWidget]:
        tabs = getattr(self._dialog_host, "tabs", None)
        if tabs is None:
            return None
        for i in range(tabs.count()):
            if tabs.tabText(i).strip().lower() == label.lower():
                w = tabs.widget(i)
                w.setParent(None)
                # v0.10.9: same dark-mode fix as SettingsSection — re-apply
                # DARK_QSS to the lifted widget so it doesn't fall back to
                # system white on Win11.
                from ..settings import DARK_QSS
                w.setObjectName("tabPage")
                w.setStyleSheet(DARK_QSS)
                return w
        return None

    def _save(self) -> None:
        try:
            self._dialog_host.apply_to(self.config)
            self.config.save()
            self._status_lbl.setText("Gespeichert ✓")
            QTimer.singleShot(2200, lambda: self._status_lbl.setText(""))
            self._on_apply()
        except Exception as exc:
            self._status_lbl.setText(f"Fehler: {exc}")
