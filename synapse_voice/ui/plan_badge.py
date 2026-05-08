"""Plan badge + Paywall dialog (v0.3.22).

Two pieces of UI for the subscription tier:

PlanBadge — small chip displayed in MainWindow's header. Shows:
    Free      → grey "Local only"
    Trial     → cyan "Trial · 4d left"
    Pro       → cyan-filled "Pro"
    Operator  → no badge

Paywall — modal dialog raised when /transcribe returns 402. Explains
the trial ended + has a single "Upgrade" CTA that opens the upgrade
URL in the user's browser.
"""
from __future__ import annotations

import webbrowser

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QColor, QPen, QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


CYAN = "#06b6d4"
CYAN_DIM = "#0e7490"
SLATE = "#94a3b8"


class PlanBadge(QWidget):
    """Compact rounded chip — paints itself, no QSS dependency."""

    def __init__(self) -> None:
        super().__init__()
        self._label = "Free"
        self._tone = "muted"  # muted | trial | pro
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(22)
        self._update_width()

    def set_state(self, label: str, tone: str) -> None:
        self._label = label
        self._tone = tone
        self._update_width()
        self.update()

    def _update_width(self) -> None:
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self._label) + 18
        self.setFixedWidth(max(w, 60))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)

        if self._tone == "pro":
            bg = QColor(CYAN)
            fg = QColor("#0f172a")
            border = QColor(CYAN)
        elif self._tone == "trial":
            bg = QColor(0, 0, 0, 0)
            fg = QColor(CYAN)
            border = QColor(CYAN)
        else:  # muted
            bg = QColor(0, 0, 0, 0)
            fg = QColor(SLATE)
            border = QColor("#334155")

        p.setBrush(bg)
        p.setPen(QPen(border, 1))
        p.drawRoundedRect(rect, 11, 11)

        f: QFont = self.font()
        f.setPointSize(max(7, f.pointSize() - 1))
        f.setBold(True)
        p.setFont(f)
        p.setPen(fg)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._label)


def _reason_text(plan: str, days_left: int) -> tuple[str, str]:
    """Pick the (label, tone) to render on the badge for a given plan."""
    if plan == "pro":
        return ("Pro", "pro")
    if plan == "trial":
        if days_left <= 0:
            return ("Trial · ended", "muted")
        if days_left == 1:
            return ("Trial · last day", "trial")
        return (f"Trial · {days_left}d left", "trial")
    if plan == "operator":
        return ("", "muted")
    return ("Local only", "muted")


def update_badge_from_info(badge: PlanBadge, info) -> None:
    """Convenience: feed the AccountInfo (or None) and set the badge."""
    if info is None:
        badge.set_state("Local only", "muted")
        return
    label, tone = _reason_text(info.plan, info.trial_days_left)
    if not label:
        badge.hide()
        return
    badge.show()
    badge.set_state(label, tone)


class PaywallDialog(QDialog):
    """Shown when /transcribe returns 402 (trial expired). One CTA →
    opens the upgrade URL in the default browser. Cancel closes."""

    def __init__(self, upgrade_url: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Upgrade to Pro")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._upgrade_url = upgrade_url

        l = QVBoxLayout(self)
        l.setContentsMargins(28, 22, 28, 22)
        l.setSpacing(14)

        title = QLabel("Your free trial has ended.")
        f = title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        title.setFont(f)
        l.addWidget(title)

        body = QLabel(
            "Upgrade to Pro to keep using the Subunit cloud — DSGVO-konform, "
            "Server in Hamburg, schneller als Local auf einem typischen Laptop. "
            "You can also switch the desktop app back to Local mode (free, "
            "fully offline) at any time in Settings."
        )
        body.setWordWrap(True)
        body.setStyleSheet("color: #94a3b8;")
        l.addWidget(body)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        upgrade = QPushButton("Upgrade to Pro")
        upgrade.setStyleSheet(
            "QPushButton { background: #06b6d4; color: #0f172a; "
            "border: none; padding: 10px 20px; border-radius: 8px; "
            "font-weight: 700; }"
            "QPushButton:hover { background: #22d3ee; }"
        )
        upgrade.clicked.connect(self._open_upgrade)
        btn_row.addWidget(upgrade, 1)

        local = QPushButton("Switch to Local")
        local.setStyleSheet(
            "QPushButton { background: transparent; color: #cbd5e1; "
            "border: 1px solid #334155; padding: 10px 16px; border-radius: 8px; }"
            "QPushButton:hover { border-color: #475569; color: #e2e8f0; }"
        )
        local.clicked.connect(self._switch_to_local)
        btn_row.addWidget(local)
        l.addLayout(btn_row)

        close = QPushButton("Maybe later")
        close.setFlat(True)
        close.setStyleSheet("color: #64748b; padding: 4px;")
        close.clicked.connect(self.reject)
        l.addWidget(close, 0, Qt.AlignmentFlag.AlignCenter)

        # Returned to caller via .result_action() so main.py can decide
        # whether to flip mode → local without us touching the config.
        self._action = "dismiss"

    def _open_upgrade(self) -> None:
        try:
            webbrowser.open(self._upgrade_url)
        except Exception:
            pass
        self._action = "upgrade"
        self.accept()

    def _switch_to_local(self) -> None:
        self._action = "local"
        self.accept()

    def result_action(self) -> str:
        """One of: dismiss | upgrade | local."""
        return self._action
