"""v0.10.0 Hub: thin wrappers that embed an existing QDialog (History,
Meetings) inline inside the Hub. We strip the Qt window flags so Qt
stops trying to render the dialog as a separate top-level window and
just lays it out as a child widget of the section."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget


class EmbeddedDialogSection(QWidget):
    """Wraps any QDialog so it can sit inside the Hub's content stack.

    The dialog still receives signals + reads its own data — we only
    flip its window-type flag so it doesn't try to draw a frame.
    """

    def __init__(self, title: str, dialog: QDialog, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(14)

        h = QLabel(title)
        h.setStyleSheet("color: #e2e8f0; font-size: 22px; font-weight: 700;")
        outer.addWidget(h)

        # Strip the dialog framing so it renders as a normal child widget.
        dialog.setWindowFlag(Qt.WindowType.Dialog, False)
        dialog.setWindowFlag(Qt.WindowType.Widget, True)
        dialog.setParent(self)
        dialog.setVisible(True)
        outer.addWidget(dialog, 1)
        self._embedded = dialog
