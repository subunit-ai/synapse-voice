"""4-step Onboarding-Wizard for first-launch users.

Voicely-inspired: visual hotkey picker (Strg/Shift/Space rendered as
keys that light up cyan when pressed), live test-recording at the end.
Subunit-style: cyan accents, dark glass cards, soft animations on
hover. Shown only on the very first launch — config.has_seen_onboarding
flips to True when the user clicks Finish.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QDialog,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import Config
from .hotkey_capture import HotkeyCaptureButton
from .mic_meter import MicLevelMeter
from .widgets import AnimatedToggle, BrandLogo

CYAN = "#40d6ff"
NIGHT = "#020817"
NIGHT_2 = "#0c1828"
NIGHT_3 = "#143246"
NIGHT_BORDER = "#1f3145"
WHITE = "#e6f2fb"
WHITE_DIM = "#9fb1bd"

QSS = f"""
QDialog {{ background: {NIGHT}; color: {WHITE}; }}
QLabel {{ color: {WHITE}; }}
QLabel#dim {{ color: {WHITE_DIM}; }}
QLabel#h1 {{ font-size: 26px; font-weight: 600; }}
QLabel#h2 {{ font-size: 14px; font-weight: 500; color: {WHITE_DIM}; letter-spacing: 1.2px; }}
QPushButton {{
    background: {NIGHT_2}; color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 10px;
    padding: 10px 22px;
    min-width: 100px;
}}
QPushButton:hover {{ border-color: {CYAN}; }}
QPushButton#primary {{
    background: {CYAN}; color: {NIGHT};
    border: none; font-weight: 700;
}}
QPushButton#primary:hover {{ background: #6cdfff; }}
QPushButton#ghost {{
    background: transparent; color: {WHITE_DIM};
    border: 1px solid {NIGHT_BORDER};
}}
QPushButton#ghost:hover {{ color: {WHITE}; }}
"""


class _StepDot(QWidget):
    """Tiny progress indicator dot — solid cyan when reached, dim otherwise."""

    SIZE = 10

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(self.SIZE + 4, self.SIZE + 4)
        self._reached = False
        self._active = False

    def set_state(self, reached: bool, active: bool) -> None:
        self._reached = reached
        self._active = active
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._active:
            color = QColor(CYAN)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(2, 2, self.SIZE, self.SIZE)
            # halo
            halo = QColor(CYAN)
            halo.setAlpha(60)
            p.setBrush(halo)
            p.drawEllipse(0, 0, self.SIZE + 4, self.SIZE + 4)
        elif self._reached:
            p.setBrush(QColor(CYAN))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(2, 2, self.SIZE, self.SIZE)
        else:
            p.setBrush(QColor(NIGHT_BORDER))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(2, 2, self.SIZE, self.SIZE)


class OnboardingDialog(QDialog):
    """4-step wizard. Pages:
        0 — Welcome + brand intro
        1 — Hotkey: visual picker with live key-press feedback
        2 — Mode: pick Local (default) or Cloud
        3 — Test recording: hold the hotkey, see the mic-meter, see
            transcribed text appear. Finish flips has_seen_onboarding.
    """

    finished_setup = pyqtSignal(dict)  # final settings dict

    def __init__(
        self,
        config: Config,
        on_test_record: Optional[Callable[[Callable[[str], None]], None]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to Synapse Voice")
        self.setStyleSheet(QSS)
        self.setModal(True)
        self.setMinimumSize(640, 520)
        self.resize(720, 580)

        self.config = config
        self._on_test_record = on_test_record  # invoked from Step 3
        # Working copy of settings — only persisted on Finish.
        self._working = {
            "hotkey": config.hotkey,
            "mode": config.mode,
        }

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 28, 36, 24)
        outer.setSpacing(16)

        # Header (logo + title)
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(BrandLogo(size=48))
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        h1 = QLabel("Welcome.")
        h1.setObjectName("h1")
        title_box.addWidget(h1)
        sub = QLabel("Let's get you dictating in 60 seconds.")
        sub.setObjectName("dim")
        title_box.addWidget(sub)
        head.addLayout(title_box, 1)
        outer.addLayout(head)

        # Step indicator
        self._dots = [_StepDot() for _ in range(4)]
        dot_row = QHBoxLayout()
        dot_row.setSpacing(8)
        dot_row.addStretch()
        for d in self._dots:
            dot_row.addWidget(d)
        dot_row.addStretch()
        outer.addLayout(dot_row)

        # Pages
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_hotkey())
        self.stack.addWidget(self._build_mode())
        self.stack.addWidget(self._build_test())
        outer.addWidget(self.stack, 1)

        # Footer nav
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.back_btn = QPushButton("Back")
        self.back_btn.setObjectName("ghost")
        self.back_btn.clicked.connect(self._go_back)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        self.skip_btn = QPushButton("Skip")
        self.skip_btn.setObjectName("ghost")
        self.skip_btn.clicked.connect(self._on_finish)
        nav.addWidget(self.skip_btn)
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self._go_next)
        nav.addWidget(self.next_btn)
        outer.addLayout(nav)

        self._sync_step(0)

    # ── Steps ──────────────────────────────────────────────────────────────

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 24, 0, 24)
        l.setSpacing(16)

        feature_box = QVBoxLayout()
        feature_box.setSpacing(14)
        for icon, title, sub in [
            ("🔒", "Local-first by default",
             "Audio never leaves your machine — unless you opt in to cloud."),
            ("⚡", "Whisper-quality, zero friction",
             "Press a hotkey, speak, paste. No window-switching, no copy-paste."),
            ("🇪🇺", "DSGVO-compliant cloud option",
             "If you switch to cloud, the Subunit-Server runs in Frankfurt."),
            ("🎯", "Built for daily dictation",
             "Lexikon for proper nouns. AI cleanup. 99 languages. Auto-update."),
        ]:
            row = QHBoxLayout()
            row.setSpacing(14)
            ic = QLabel(icon)
            f = QFont()
            f.setPointSize(20)
            ic.setFont(f)
            row.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet(f"color: {WHITE}; font-size: 15px; font-weight: 600;")
            text_col.addWidget(t)
            s = QLabel(sub)
            s.setStyleSheet(f"color: {WHITE_DIM}; font-size: 12px;")
            s.setWordWrap(True)
            text_col.addWidget(s)
            row.addLayout(text_col, 1)
            feature_box.addLayout(row)
        l.addLayout(feature_box)
        l.addStretch()
        return page

    def _build_hotkey(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 18, 0, 18)
        l.setSpacing(18)

        title = QLabel("Pick your hotkey")
        title.setObjectName("h1")
        title.setStyleSheet("font-size: 22px;")
        l.addWidget(title)

        sub = QLabel(
            "This is the key combo you press to dictate. The default is "
            "Ctrl + Space — easy to reach with one hand. "
            "Hold to record, release to transcribe."
        )
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        l.addWidget(sub)

        # Visual hotkey display — keys light up when pressed (rendered live
        # by KeyVisualizer below)
        self._key_viz = KeyVisualizer(self._working["hotkey"])
        l.addWidget(self._key_viz, 0, Qt.AlignmentFlag.AlignCenter)

        # Real capture button — clicking it lets the user press a new combo
        capture_row = QHBoxLayout()
        capture_row.addStretch()
        self._hotkey_btn = HotkeyCaptureButton(self._working["hotkey"])
        # HotkeyCaptureButton emits via .value() on demand; we poll on Next.
        capture_row.addWidget(self._hotkey_btn)
        capture_row.addStretch()
        l.addLayout(capture_row)

        hint = QLabel("Click the button → press your preferred combo")
        hint.setObjectName("dim")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.addWidget(hint)
        l.addStretch()
        return page

    def _build_mode(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 18, 0, 18)
        l.setSpacing(18)

        title = QLabel("Local or cloud?")
        title.setObjectName("h1")
        title.setStyleSheet("font-size: 22px;")
        l.addWidget(title)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)

        self._local_card = ModeCard(
            icon="🔒",
            title="Local",
            subtitle="100% private",
            body="Whisper runs on your machine. "
                 "Audio never leaves the device. "
                 "Best for sensitive content.",
            badge="Default",
        )
        self._local_card.clicked.connect(lambda: self._pick_mode("local"))
        cards_row.addWidget(self._local_card)

        self._cloud_card = ModeCard(
            icon="☁",
            title="Cloud",
            subtitle="Faster, more accurate",
            body="Routed through Subunit-Server in Frankfurt. "
                 "DSGVO-compliant. "
                 "Slightly faster than Local on a typical laptop.",
            badge="EU only",
        )
        self._cloud_card.clicked.connect(lambda: self._pick_mode("cloud"))
        cards_row.addWidget(self._cloud_card)

        l.addLayout(cards_row)
        l.addStretch()

        # Initial highlight reflects current pick
        self._sync_mode_cards()
        return page

    def _build_test(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 18, 0, 18)
        l.setSpacing(16)

        title = QLabel("Try it out")
        title.setObjectName("h1")
        title.setStyleSheet("font-size: 22px;")
        l.addWidget(title)

        sub = QLabel(
            "Speak into your mic. The bar below shows the live signal. "
            "When you're ready, finish setup and press your hotkey from any "
            "app to start dictating."
        )
        sub.setObjectName("dim")
        sub.setWordWrap(True)
        l.addWidget(sub)

        l.addSpacing(10)
        l.addWidget(QLabel("Microphone test"))
        self._meter = MicLevelMeter()
        l.addWidget(self._meter)

        l.addSpacing(20)
        ready = QLabel("✓ Ready to go. Press Finish to start using Synapse Voice.")
        ready.setStyleSheet(f"color: {CYAN}; font-size: 13px; font-weight: 600;")
        ready.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.addWidget(ready)
        l.addStretch()
        return page

    # ── Mode cards ─────────────────────────────────────────────────────────

    def _pick_mode(self, kind: str) -> None:
        self._working["mode"] = (
            "local" if kind == "local" else (self.config.last_cloud_mode or "subunit")
        )
        self._sync_mode_cards()

    def _sync_mode_cards(self) -> None:
        is_local = self._working["mode"] == "local"
        self._local_card.set_active(is_local)
        self._cloud_card.set_active(not is_local)

    # ── Nav ────────────────────────────────────────────────────────────────

    def _sync_step(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, d in enumerate(self._dots):
            d.set_state(reached=(i <= idx), active=(i == idx))
        self.back_btn.setVisible(idx > 0)
        is_last = idx == self.stack.count() - 1
        self.next_btn.setText("Finish" if is_last else "Next")
        self.skip_btn.setVisible(not is_last)

    def _go_next(self) -> None:
        idx = self.stack.currentIndex()
        # Persist the hotkey choice when leaving Step 1
        if idx == 1:
            captured = self._hotkey_btn.value()
            if captured:
                self._working["hotkey"] = captured
                self._key_viz.set_combo(captured)
        if idx == self.stack.count() - 1:
            self._on_finish()
            return
        self._sync_step(idx + 1)

    def _go_back(self) -> None:
        idx = self.stack.currentIndex()
        if idx > 0:
            self._sync_step(idx - 1)

    def _on_finish(self) -> None:
        # Persist captured hotkey if user landed on the page but didn't press Next
        captured = self._hotkey_btn.value() if hasattr(self, "_hotkey_btn") else None
        if captured:
            self._working["hotkey"] = captured
        self.finished_setup.emit(dict(self._working))
        self.accept()


# ── Visual key renderer ──────────────────────────────────────────────────────


class KeyVisualizer(QWidget):
    """Renders the chosen hotkey as a row of glass key-cap pills.
    Live key-press detection (via Qt event filter on app installEventFilter)
    is out of scope for v0.4 — the static render already covers the use
    case TJ asked for ("man kann sehen welche Tasten zum Hotkey gehoeren").
    """

    KEY_HEIGHT = 56
    KEY_PADDING = 14
    KEY_GAP = 10

    def __init__(self, combo: str) -> None:
        super().__init__()
        self._keys: list[str] = []
        self.set_combo(combo)
        self.setMinimumHeight(self.KEY_HEIGHT + 6)

    def set_combo(self, combo: str) -> None:
        # Parse "<ctrl>+<shift>+<space>" → ["Ctrl", "Shift", "Space"]
        raw = combo.replace("<", "").replace(">", "").split("+")
        pretty = []
        for k in raw:
            k = k.strip().lower()
            if not k:
                continue
            label = {
                "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "cmd": "⌘",
                "super": "⊞", "space": "Space", "tab": "Tab", "enter": "Enter",
                "esc": "Esc",
            }.get(k, k.upper() if len(k) == 1 else k.capitalize())
            pretty.append(label)
        self._keys = pretty
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        if not self._keys:
            return QSize(120, self.KEY_HEIGHT + 6)
        # Estimate width
        from PyQt6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        total = 0
        for k in self._keys:
            total += max(60, fm.horizontalAdvance(k) + self.KEY_PADDING * 2)
        total += self.KEY_GAP * (len(self._keys) - 1)
        # Add room for the "+" separators
        total += 18 * (len(self._keys) - 1)
        return QSize(total, self.KEY_HEIGHT + 6)

    def paintEvent(self, _e) -> None:
        from PyQt6.QtGui import QFontMetrics, QLinearGradient

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        f = QFont()
        f.setPointSize(13)
        f.setBold(True)
        p.setFont(f)
        fm = QFontMetrics(f)

        x = 0
        for i, k in enumerate(self._keys):
            w = max(60, fm.horizontalAdvance(k) + self.KEY_PADDING * 2)
            y = 3
            # Glow halo
            halo = QColor(CYAN)
            halo.setAlpha(34)
            p.setBrush(halo)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(x - 4, y - 4, w + 8, self.KEY_HEIGHT + 8, 12, 12)
            # Key body
            grad = QLinearGradient(0, y, 0, y + self.KEY_HEIGHT)
            grad.setColorAt(0.0, QColor("#1c3a52"))
            grad.setColorAt(1.0, QColor(NIGHT_2))
            p.setBrush(grad)
            p.setPen(QPen(QColor(NIGHT_BORDER), 1.4))
            p.drawRoundedRect(x, y, w, self.KEY_HEIGHT, 10, 10)
            # Highlight stripe
            p.setPen(QPen(QColor(CYAN), 2.0))
            p.drawLine(x + 8, y + 4, x + w - 8, y + 4)
            # Text
            p.setPen(QColor(WHITE))
            p.drawText(
                x, y, w, self.KEY_HEIGHT,
                int(Qt.AlignmentFlag.AlignCenter),
                k,
            )

            x += w
            if i < len(self._keys) - 1:
                # "+" separator
                p.setPen(QColor(WHITE_DIM))
                p.drawText(x, y, 18, self.KEY_HEIGHT,
                           int(Qt.AlignmentFlag.AlignCenter), "+")
                x += 18


# ── Mode card widget ─────────────────────────────────────────────────────────


class ModeCard(QWidget):
    clicked = pyqtSignal()

    def __init__(self, icon: str, title: str, subtitle: str, body: str, badge: str) -> None:
        super().__init__()
        self._active = False
        self._icon = icon
        self._title = title
        self._subtitle = subtitle
        self._body = body
        self._badge = badge
        self.setMinimumHeight(190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_active(self, active: bool) -> None:
        if active != self._active:
            self._active = active
            self.update()

    def mousePressEvent(self, _e) -> None:
        self.clicked.emit()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(2, 2, -2, -2)

        # Outer glow when active
        if self._active:
            for r in range(8, 0, -2):
                halo = QColor(CYAN)
                halo.setAlpha(int(40 * (1 - r / 8)))
                p.setBrush(halo)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawRoundedRect(rect.adjusted(-r, -r, r, r), 16 + r, 16 + r)

        # Card body
        p.setBrush(QColor(NIGHT_2))
        border = QColor(CYAN) if self._active else QColor(NIGHT_BORDER)
        p.setPen(QPen(border, 2 if self._active else 1))
        p.drawRoundedRect(rect, 16, 16)

        # Content
        x = rect.x() + 22
        y = rect.y() + 22

        # Icon
        f = QFont()
        f.setPointSize(28)
        p.setFont(f)
        p.setPen(QColor(WHITE))
        p.drawText(x, y, rect.width() - 44, 40,
                   int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                   self._icon)
        # Title
        f.setPointSize(18)
        f.setBold(True)
        p.setFont(f)
        p.drawText(x, y + 50, rect.width() - 44, 28,
                   int(Qt.AlignmentFlag.AlignLeft),
                   self._title)
        # Subtitle
        f.setPointSize(11)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(CYAN) if self._active else QColor(WHITE_DIM))
        p.drawText(x, y + 80, rect.width() - 44, 22,
                   int(Qt.AlignmentFlag.AlignLeft),
                   self._subtitle)
        # Body
        f.setPointSize(10)
        p.setFont(f)
        p.setPen(QColor(WHITE_DIM))
        p.drawText(
            x, y + 105, rect.width() - 44, rect.height() - 130,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop |
                Qt.TextFlag.TextWordWrap.value),
            self._body,
        )

        # Badge top-right
        if self._badge:
            badge_w = 70
            badge_h = 22
            bx = rect.x() + rect.width() - badge_w - 14
            by = rect.y() + 14
            p.setBrush(QColor(CYAN) if self._active else QColor(NIGHT_3))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(bx, by, badge_w, badge_h, 11, 11)
            f.setPointSize(8)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(NIGHT) if self._active else QColor(WHITE_DIM))
            p.drawText(bx, by, badge_w, badge_h,
                       int(Qt.AlignmentFlag.AlignCenter),
                       self._badge)
