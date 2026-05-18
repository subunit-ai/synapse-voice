"""Section placeholders used by the Hub until Phase 2/3 ship the
real inline pages. They render a centred message + an optional CTA
that opens the legacy dialog, so the app stays fully functional
during the refactor instead of waiting on a complete drop."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget


WHITE = "#e2e8f0"
WHITE_DIM = "#94a3b8"
CYAN = "#06b6d4"


class PlaceholderSection(QWidget):
    def __init__(
        self,
        title: str,
        message: str,
        cta_label: Optional[str] = None,
        cta_callback: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 60, 40, 60)
        outer.setSpacing(14)
        outer.addStretch(1)

        h = QLabel(title)
        h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h.setStyleSheet(f"color: {WHITE}; font-size: 22px; font-weight: 700;")
        outer.addWidget(h)

        m = QLabel(message)
        m.setAlignment(Qt.AlignmentFlag.AlignCenter)
        m.setWordWrap(True)
        m.setStyleSheet(f"color: {WHITE_DIM}; font-size: 14px;")
        outer.addWidget(m)

        if cta_label and cta_callback is not None:
            btn = QPushButton(cta_label)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(40)
            btn.setMinimumWidth(220)
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {CYAN}; color: #031426;
                    border: none; border-radius: 8px;
                    padding: 8px 22px; font-weight: 700;
                }}
                QPushButton:hover {{ background: #22d3ee; }}
                """
            )
            btn.clicked.connect(lambda _=False: cta_callback())
            row = QVBoxLayout()
            row.setAlignment(Qt.AlignmentFlag.AlignCenter)
            outer.addSpacing(6)
            outer.addWidget(btn, 0, Qt.AlignmentFlag.AlignCenter)

        outer.addStretch(2)
