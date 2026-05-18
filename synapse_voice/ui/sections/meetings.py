"""v0.10.0 Hub: Meetings section — embeds the existing MeetingsDialog
inline. The dialog already manages its own list, action-item extraction
and recap-email flow, so we only need the layout wrap."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...config import Config


WHITE = "#e2e8f0"
WHITE_DIM = "#94a3b8"


class MeetingsSection(QWidget):
    def __init__(self, config: Config, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        h = QLabel("Meetings")
        h.setStyleSheet(f"color: {WHITE}; font-size: 22px; font-weight: 700;")
        outer.addWidget(h)
        sub = QLabel("Long-form Aufnahmen (≥ 4 Min) mit Action-Items + Recap-Email.")
        sub.setStyleSheet(f"color: {WHITE_DIM}; font-size: 13px;")
        outer.addWidget(sub)

        try:
            from ..meetings import MeetingsDialog
            self._dlg = MeetingsDialog(config)
            self._dlg.setWindowFlag(Qt.WindowType.Dialog, False)
            self._dlg.setWindowFlag(Qt.WindowType.Widget, True)
            self._dlg.setParent(self)
            self._dlg.setVisible(True)
            outer.addWidget(self._dlg, 1)
        except Exception as exc:
            err = QLabel(f"Meetings konnten nicht geladen werden: {exc}")
            err.setStyleSheet(f"color: {WHITE_DIM}; padding: 24px;")
            err.setWordWrap(True)
            outer.addWidget(err, 1)
