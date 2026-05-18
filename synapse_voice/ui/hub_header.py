"""v0.10.0 Hub header — Sonar wordmark + version on the left, plan
badge + profile avatar on the right. The plan badge re-uses the
existing PlanBadge widget so the colour-coded tier stays consistent
with everywhere else; the avatar is a new round-initial widget that
calls back into the Hub when clicked so the user can jump to the
Account section without going through Settings."""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .plan_badge import PlanBadge
from .widgets import BrandLogo


CYAN = "#06b6d4"
WHITE = "#e2e8f0"
WHITE_DIM = "#94a3b8"
SURFACE = "#1e293b"


class ProfileAvatar(QPushButton):
    """Round-initial avatar. Click bubbles up as a Qt signal so the
    Hub can swap to its Account section."""

    clicked_avatar = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._initial = "?"
        self._email = ""
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(36, 36)
        self.setToolTip("Account")
        self.clicked.connect(self.clicked_avatar.emit)
        self.setStyleSheet("QPushButton { border: none; background: transparent; }")

    def set_email(self, email: str) -> None:
        self._email = (email or "").strip()
        self._initial = (self._email[:1].upper() or "?")
        self.setToolTip(f"Account — {self._email}" if self._email else "Account")
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        d = min(self.width(), self.height())
        rect = self.rect().adjusted(0, 0, -1, -1)

        # Filled circle
        path = QPainterPath()
        path.addEllipse(0, 0, d - 1, d - 1)
        p.fillPath(path, QColor(CYAN))

        # Rim
        p.setPen(QPen(QColor("#0e7490"), 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(0, 0, d - 1, d - 1)

        # Initial letter, centred
        p.setPen(QColor("#031426"))
        f = p.font()
        f.setBold(True)
        f.setPixelSize(int(d * 0.42))
        p.setFont(f)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._initial)


class HubHeader(QWidget):
    """The thin top bar that lives above every Hub section."""

    profile_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("hubHeader")
        self.setFixedHeight(64)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setStyleSheet(
            "QWidget#hubHeader { background: #0b1426; border-bottom: 1px solid #1e293b; }"
        )

        row = QHBoxLayout(self)
        row.setContentsMargins(20, 8, 18, 8)
        row.setSpacing(14)

        # Brand: tiny logo + wordmark + version
        row.addWidget(BrandLogo(size=36))
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(0)
        title = QLabel("Sonar")
        title.setStyleSheet(
            f"color: {WHITE}; font-size: 18px; font-weight: 700; letter-spacing: -0.2px;"
        )
        version = QLabel(f"v{__version__}")
        version.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 0.6px;")
        title_box.addWidget(title)
        title_box.addWidget(version)
        row.addLayout(title_box)
        row.addStretch()

        # Plan badge — re-uses the existing colour-coded widget. Hub keeps
        # a reference so main.py can call header.plan_badge.set_plan(...).
        self.plan_badge = PlanBadge()
        self.plan_badge.hide()  # surfaced after first /account/info refresh
        row.addWidget(self.plan_badge, 0, Qt.AlignmentFlag.AlignVCenter)

        # Profile avatar
        self.avatar = ProfileAvatar(self)
        self.avatar.clicked_avatar.connect(self.profile_clicked.emit)
        row.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_account(self, email: str) -> None:
        """Sync the avatar initial when /account/info returns."""
        self.avatar.set_email(email)
