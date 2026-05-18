"""v0.10.0 Hub: Verlauf (History) section — embeds the existing
HistoryDialog inline. Same trick as SettingsSection: instantiate the
dialog with window-flags stripped so Qt lays it out as a child
widget instead of opening a separate window."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...config import Config


WHITE = "#e2e8f0"
WHITE_DIM = "#94a3b8"


class HistorySection(QWidget):
    def __init__(
        self,
        config: Config,
        on_repaste: Callable[[str], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        h = QLabel("Verlauf")
        h.setStyleSheet(f"color: {WHITE}; font-size: 22px; font-weight: 700;")
        outer.addWidget(h)
        sub = QLabel("Doppelklick auf eine Zeile kopiert sie in die Zwischenablage.")
        sub.setStyleSheet(f"color: {WHITE_DIM}; font-size: 13px;")
        outer.addWidget(sub)

        try:
            from ..history import HistoryDialog
            self._dlg = HistoryDialog(config, on_repaste=on_repaste)
            # Strip dialog framing so it lays out as a child widget.
            self._dlg.setWindowFlag(Qt.WindowType.Dialog, False)
            self._dlg.setWindowFlag(Qt.WindowType.Widget, True)
            self._dlg.setParent(self)
            self._dlg.setVisible(True)
            outer.addWidget(self._dlg, 1)
        except Exception as exc:
            err = QLabel(f"Verlauf konnte nicht geladen werden: {exc}")
            err.setStyleSheet(f"color: {WHITE_DIM}; padding: 24px;")
            err.setWordWrap(True)
            outer.addWidget(err, 1)
