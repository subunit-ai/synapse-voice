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
    QObject,
    QPropertyAnimation,
    QSize,
    Qt,
    QThread,
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import i18n
from ..config import Config
from .hotkey_capture import HotkeyCaptureButton
from .mic_meter import MicLevelMeter
from .widgets import AnimatedToggle, BrandLogo

tr = i18n.tr

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


class LanguageToggle(QWidget):
    """2-segment DE | EN toggle for the onboarding header. Subunit-cyan
    active half, dim other half. Clicking either half flips the
    selection and emits .changed(lang)."""

    changed = pyqtSignal(str)

    def __init__(self, current: str) -> None:
        super().__init__()
        self.setFixedSize(74, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._current = current if current in ("de", "en") else "de"

    def mousePressEvent(self, e) -> None:
        # Left half = DE, right half = EN
        new = "de" if e.position().x() < self.width() / 2 else "en"
        if new != self._current:
            self._current = new
            self.update()
            self.changed.emit(new)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect()
        # Pill background
        p.setBrush(QColor(NIGHT_2))
        p.setPen(QPen(QColor(NIGHT_BORDER), 1))
        p.drawRoundedRect(rect, 14, 14)
        half_w = rect.width() // 2
        # Active highlight
        active_x = rect.x() + 2 if self._current == "de" else rect.x() + half_w
        active_w = half_w - 2
        p.setBrush(QColor(CYAN))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(active_x, rect.y() + 2, active_w, rect.height() - 4, 12, 12)
        # Labels
        f = QFont()
        f.setPointSize(9)
        f.setBold(True)
        p.setFont(f)
        for i, label in enumerate(["DE", "EN"]):
            is_active = (i == 0 and self._current == "de") or (i == 1 and self._current == "en")
            p.setPen(QColor(NIGHT) if is_active else QColor(WHITE_DIM))
            x = rect.x() + i * half_w
            p.drawText(x, rect.y(), half_w, rect.height(),
                       int(Qt.AlignmentFlag.AlignCenter), label)


def _qt_key_to_token(e) -> Optional[str]:
    """Map a Qt KeyEvent to the same canonical token format we use in
    config.hotkey ("ctrl", "shift", "alt", "space", "a", "1", ...).
    Modifiers come from `e.key()` on the press of the modifier itself
    (not the bitmask) so live-tracking shows them lighting up one by one.
    """
    k = e.key()
    if k == Qt.Key.Key_Control:
        return "ctrl"
    if k == Qt.Key.Key_Shift:
        return "shift"
    if k == Qt.Key.Key_Alt:
        return "alt"
    if k == Qt.Key.Key_Meta:
        return "cmd"
    if k == Qt.Key.Key_Space:
        return "space"
    if k == Qt.Key.Key_Tab:
        return "tab"
    if k == Qt.Key.Key_Return or k == Qt.Key.Key_Enter:
        return "enter"
    if k == Qt.Key.Key_Escape:
        return "esc"
    # Letters / digits: use the text representation
    txt = e.text()
    if txt and txt.isalnum() and len(txt) == 1:
        return txt.lower()
    return None


class WelcomeHero(QWidget):
    """A breathing cyan orb that anchors the welcome page. Subtly
    animated so the page feels alive without being busy. Replaces the
    "altbacken" feeling TJ flagged on v0.3.15."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(180, 110)
        self._phase = 0.0
        self._tick = QTimer(self)
        self._tick.setInterval(33)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

    def _on_tick(self) -> None:
        import math as _m

        self._phase += 0.04
        self.update()

    def paintEvent(self, _e) -> None:
        import math as _m

        from PyQt6.QtGui import QRadialGradient

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx = self.width() // 2
        cy = self.height() // 2 + 4
        r = 36
        breath = 0.5 + 0.5 * _m.sin(self._phase * 0.8)

        # Outer halo rings
        for i in range(28, 0, -3):
            alpha = int(36 * breath * (1 - i / 28))
            color = QColor(64, 214, 255, alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(cx - r - i, cy - r - i, (r + i) * 2, (r + i) * 2)

        # Core orb
        grad = QRadialGradient(cx - 6, cy - 8, r * 1.4)
        grad.setColorAt(0.0, QColor(120, 230, 255))
        grad.setColorAt(0.6, QColor(64, 214, 255))
        grad.setColorAt(1.0, QColor(20, 96, 130))
        from PyQt6.QtGui import QBrush

        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(255, 255, 255, 90), 1.0))
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

        # Inner pulse
        inner = max(6, int(12 + 4 * _m.sin(self._phase * 1.5)))
        p.setBrush(QColor(255, 255, 255, 200))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - inner, cy - inner, inner * 2, inner * 2)

        # Specular
        p.setBrush(QColor(255, 255, 255, 130))
        p.drawEllipse(cx - 18, cy - 22, 12, 12)


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
        self.setWindowTitle("Sonar")
        self.setStyleSheet(QSS)
        self.setModal(True)
        self.setMinimumSize(640, 540)
        self.resize(720, 600)

        self.config = config
        self._on_test_record = on_test_record  # invoked from Step 3
        # Working copy of settings — only persisted on Finish.
        self._working = {
            "hotkey": config.hotkey,
            "mode": config.mode,
            "ui_language": config.ui_language or "de",
            "ui_theme": config.ui_theme or "dark",
            "account_email": config.account_email or "",
            "subunit_api_key": config.subunit_api_key or "",
            "plan": config.plan or "free",
            "trial_started_at": config.trial_started_at or 0,
            # v0.3.26: Auto-Mode advertised in the onboarding — flips
            # cleanup_auto_mode + cleanup_enabled when the user opts in.
            "cleanup_auto_mode": config.cleanup_auto_mode,
            "cleanup_enabled": config.cleanup_enabled,
        }
        # Live-key tracking for the Hotkey page's KeyVisualizer
        self._pressed_keys: set[str] = set()
        # Account-step state (worker thread for /sign-up)
        self._signup_thread = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(36, 28, 36, 24)
        outer.setSpacing(16)

        # Header (logo + title + DE/EN toggle on the right)
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(BrandLogo(size=48))
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.h1_lbl = QLabel(tr("onb.welcome.title"))
        self.h1_lbl.setObjectName("h1")
        title_box.addWidget(self.h1_lbl)
        self.h1_sub = QLabel(tr("onb.welcome.sub"))
        self.h1_sub.setObjectName("dim")
        title_box.addWidget(self.h1_sub)
        head.addLayout(title_box, 1)

        # Language toggle: 2-segment DE | EN at the top-right.
        self.lang_toggle = LanguageToggle(self._working["ui_language"])
        self.lang_toggle.changed.connect(self._on_language_change)
        head.addWidget(self.lang_toggle, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        # Pages — built first so we know how many step-dots to render.
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_welcome())
        self.stack.addWidget(self._build_account())
        self.stack.addWidget(self._build_theme())
        self.stack.addWidget(self._build_hotkey())
        self.stack.addWidget(self._build_mode())
        self.stack.addWidget(self._build_auto_mode())
        self.stack.addWidget(self._build_test())

        # Step indicator
        self._dots = [_StepDot() for _ in range(self.stack.count())]
        dot_row = QHBoxLayout()
        dot_row.setSpacing(8)
        dot_row.addStretch()
        for d in self._dots:
            dot_row.addWidget(d)
        dot_row.addStretch()
        outer.addLayout(dot_row)

        outer.addWidget(self.stack, 1)

        # Footer nav
        nav = QHBoxLayout()
        nav.setSpacing(8)
        self.back_btn = QPushButton(tr("onb.btn.back"))
        self.back_btn.setObjectName("ghost")
        self.back_btn.clicked.connect(self._go_back)
        nav.addWidget(self.back_btn)
        nav.addStretch()
        self.skip_btn = QPushButton(tr("onb.btn.skip"))
        self.skip_btn.setObjectName("ghost")
        self.skip_btn.clicked.connect(self._on_finish)
        nav.addWidget(self.skip_btn)
        self.next_btn = QPushButton(tr("onb.btn.next"))
        self.next_btn.setObjectName("primary")
        self.next_btn.clicked.connect(self._go_next)
        nav.addWidget(self.next_btn)
        outer.addLayout(nav)

        self._sync_step(0)

    # ── Steps ──────────────────────────────────────────────────────────────

    def _build_welcome(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 18, 0, 18)
        l.setSpacing(14)

        # Hero glyph with cyan halo — gives the page some visual weight
        # so it doesn't read as just a feature-list dump.
        hero = WelcomeHero()
        l.addWidget(hero, 0, Qt.AlignmentFlag.AlignCenter)

        # Feature rows fade in staggered when the page first appears.
        # Stored on the dialog so re-entering the page replays the
        # animation.
        self._welcome_rows: list[QWidget] = []
        feature_box = QVBoxLayout()
        feature_box.setSpacing(12)
        features = [
            ("🔒", tr("onb.feature.local.title"), tr("onb.feature.local.body")),
            ("⚡", tr("onb.feature.quality.title"), tr("onb.feature.quality.body")),
            ("🇪🇺", tr("onb.feature.dsgvo.title"), tr("onb.feature.dsgvo.body")),
            ("🎯", tr("onb.feature.daily.title"), tr("onb.feature.daily.body")),
        ]
        for icon, title, sub in features:
            row = QWidget()
            rh = QHBoxLayout(row)
            rh.setContentsMargins(0, 0, 0, 0)
            rh.setSpacing(14)
            ic = QLabel(icon)
            f = QFont()
            f.setPointSize(20)
            ic.setFont(f)
            rh.addWidget(ic, 0, Qt.AlignmentFlag.AlignTop)
            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet(f"color: {WHITE}; font-size: 15px; font-weight: 600;")
            text_col.addWidget(t)
            s = QLabel(sub)
            s.setStyleSheet(f"color: {WHITE_DIM}; font-size: 12px;")
            s.setWordWrap(True)
            text_col.addWidget(s)
            rh.addLayout(text_col, 1)
            feature_box.addWidget(row)
            # Wire each row with its own opacity effect so we can
            # cascade fade-ins.
            eff = QGraphicsOpacityEffect(row)
            eff.setOpacity(0.0)
            row.setGraphicsEffect(eff)
            row._opacity_effect = eff
            self._welcome_rows.append(row)
        l.addLayout(feature_box)
        l.addStretch()
        return page

    def _animate_welcome_in(self) -> None:
        """Cascade fade-in of the feature rows after the welcome page is
        shown. Each row delayed by ~80ms so the eye is drawn down the list."""
        # Keep refs to anims so they're not garbage-collected mid-animation
        if not hasattr(self, "_welcome_anims"):
            self._welcome_anims = []
        self._welcome_anims.clear()
        for i, row in enumerate(getattr(self, "_welcome_rows", [])):
            eff = getattr(row, "_opacity_effect", None)
            if eff is None:
                continue
            eff.setOpacity(0.0)
            anim = QPropertyAnimation(eff, b"opacity", self)
            anim.setDuration(420)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            QTimer.singleShot(80 * i, anim.start)
            self._welcome_anims.append(anim)

    def _build_account(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 14, 0, 14)
        l.setSpacing(14)

        self._acc_title = QLabel(tr("onb.account.title"))
        self._acc_title.setObjectName("h1")
        self._acc_title.setStyleSheet("font-size: 22px;")
        l.addWidget(self._acc_title)

        self._acc_sub = QLabel(tr("onb.account.sub"))
        self._acc_sub.setObjectName("dim")
        self._acc_sub.setWordWrap(True)
        l.addWidget(self._acc_sub)

        # Email field
        from PyQt6.QtWidgets import QLineEdit, QFrame

        self._acc_email_lbl = QLabel(tr("onb.account.email_label"))
        self._acc_email_lbl.setObjectName("dim")
        l.addWidget(self._acc_email_lbl)

        self._acc_email = QLineEdit()
        self._acc_email.setPlaceholderText(tr("onb.account.email_placeholder"))
        self._acc_email.setText(self._working.get("account_email", ""))
        self._acc_email.setStyleSheet(
            "QLineEdit { font-size: 15px; padding: 10px 14px; "
            "background: rgba(255,255,255,0.04); "
            "border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; "
            "color: #e2e8f0; } "
            "QLineEdit:focus { border-color: #06b6d4; }"
        )
        self._acc_email.returnPressed.connect(self._on_account_primary)
        l.addWidget(self._acc_email)

        # Code input — hidden until the user has requested a code.
        # Six digits, monospaced, comfortable spacing — reads as "type
        # the number from the email here".
        self._acc_code_lbl = QLabel(tr("onb.account.code_label"))
        self._acc_code_lbl.setObjectName("dim")
        self._acc_code_lbl.setVisible(False)
        l.addWidget(self._acc_code_lbl)

        self._acc_code = QLineEdit()
        self._acc_code.setPlaceholderText("000000")
        self._acc_code.setMaxLength(6)
        self._acc_code.setStyleSheet(
            "QLineEdit { font-family: 'SF Mono','Menlo','Consolas',monospace; "
            "font-size: 22px; letter-spacing: 0.32em; padding: 10px 14px; "
            "background: rgba(255,255,255,0.04); "
            "border: 1px solid rgba(255,255,255,0.10); border-radius: 8px; "
            "color: #e2e8f0; } "
            "QLineEdit:focus { border-color: #06b6d4; }"
        )
        self._acc_code.returnPressed.connect(self._on_account_primary)
        self._acc_code.setVisible(False)
        l.addWidget(self._acc_code)

        # Primary button.  Text + handler change with the flow stage.
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._acc_signup_btn = QPushButton(tr("onb.account.btn.request_code"))
        self._acc_signup_btn.setObjectName("primary")
        self._acc_signup_btn.setStyleSheet(
            "QPushButton#primary { font-weight: 700; padding: 10px 18px; }"
        )
        self._acc_signup_btn.clicked.connect(self._on_account_primary)
        btn_row.addWidget(self._acc_signup_btn, 1)
        l.addLayout(btn_row)

        # "Code nochmal senden" — visible only in stage 2.
        self._acc_resend_btn = QPushButton(tr("onb.account.btn.resend"))
        self._acc_resend_btn.setObjectName("ghost")
        self._acc_resend_btn.setStyleSheet(
            "QPushButton#ghost { font-size: 13px; padding: 4px 8px; }"
        )
        self._acc_resend_btn.setFlat(True)
        self._acc_resend_btn.clicked.connect(self._on_account_resend)
        self._acc_resend_btn.setVisible(False)
        l.addWidget(self._acc_resend_btn, 0, Qt.AlignmentFlag.AlignLeft)

        # Status (hidden until we have something to say)
        self._acc_status = QLabel("")
        self._acc_status.setObjectName("dim")
        self._acc_status.setWordWrap(True)
        self._acc_status.setStyleSheet("padding: 4px 0;")
        l.addWidget(self._acc_status)

        # State machine: "email" (collect email + request code)
        # or "code" (collect 6-digit code + verify).  Defaults to email.
        self._acc_stage = "email"

        # Benefit list — three short rows with cyan dot prefixes
        l.addSpacing(8)
        self._acc_benefits = []
        for key in (
            "onb.account.benefit.privacy",
            "onb.account.benefit.eu",
            "onb.account.benefit.cancel",
        ):
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = QLabel("●")
            dot.setStyleSheet("color: #06b6d4; font-size: 14px;")
            row.addWidget(dot)
            txt = QLabel(tr(key))
            txt.setObjectName("dim")
            txt.setWordWrap(True)
            row.addWidget(txt, 1)
            l.addLayout(row)
            self._acc_benefits.append((key, txt))

        # Skip link at the bottom
        l.addStretch()
        self._acc_skip_btn = QPushButton(tr("onb.account.btn.skip"))
        self._acc_skip_btn.setObjectName("ghost")
        self._acc_skip_btn.setStyleSheet(
            "QPushButton#ghost { color: #94a3b8; padding: 6px 0; "
            "background: transparent; border: none; text-decoration: underline; }"
            "QPushButton#ghost:hover { color: #cbd5e1; }"
        )
        self._acc_skip_btn.clicked.connect(self._on_account_skip)
        l.addWidget(self._acc_skip_btn, 0, Qt.AlignmentFlag.AlignCenter)

        return page

    def _build_theme(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 18, 0, 18)
        l.setSpacing(18)

        self._theme_title = QLabel(tr("onb.theme.title"))
        self._theme_title.setObjectName("h1")
        self._theme_title.setStyleSheet("font-size: 22px;")
        l.addWidget(self._theme_title)

        self._theme_sub = QLabel(tr("onb.theme.sub"))
        self._theme_sub.setObjectName("dim")
        self._theme_sub.setWordWrap(True)
        l.addWidget(self._theme_sub)

        # Cards row
        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)

        self._theme_dark_card = ModeCard(
            icon="🌙",
            title=tr("onb.theme.dark"),
            subtitle="",
            body=tr("onb.theme.dark.body"),
            badge="Default",
        )
        self._theme_dark_card.clicked.connect(lambda: self._pick_theme("dark"))
        cards_row.addWidget(self._theme_dark_card)

        self._theme_light_card = ModeCard(
            icon="☀",
            title=tr("onb.theme.light"),
            subtitle="",
            body=tr("onb.theme.light.body"),
            badge="",
        )
        self._theme_light_card.clicked.connect(lambda: self._pick_theme("light"))
        cards_row.addWidget(self._theme_light_card)

        l.addLayout(cards_row)
        l.addStretch()

        self._sync_theme_cards()
        return page

    def _build_hotkey(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 18, 0, 18)
        l.setSpacing(18)

        self._hotkey_title = QLabel(tr("onb.hotkey.title"))
        self._hotkey_title.setObjectName("h1")
        self._hotkey_title.setStyleSheet("font-size: 22px;")
        l.addWidget(self._hotkey_title)

        self._hotkey_sub = QLabel(tr("onb.hotkey.sub"))
        self._hotkey_sub.setObjectName("dim")
        self._hotkey_sub.setWordWrap(True)
        l.addWidget(self._hotkey_sub)

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

        self._hotkey_hint = QLabel(tr("onb.hotkey.hint"))
        self._hotkey_hint.setObjectName("dim")
        self._hotkey_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.addWidget(self._hotkey_hint)

        # Live hint — flashes cyan briefly when the user presses a key
        self._hotkey_live_hint = QLabel(tr("onb.hotkey.live_hint"))
        self._hotkey_live_hint.setObjectName("dim")
        self._hotkey_live_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hotkey_live_hint.setStyleSheet(f"color: {CYAN}; font-size: 12px;")
        l.addWidget(self._hotkey_live_hint)
        l.addStretch()
        return page

    def _build_mode(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 18, 0, 18)
        l.setSpacing(18)

        self._mode_title = QLabel(tr("onb.mode.title"))
        self._mode_title.setObjectName("h1")
        self._mode_title.setStyleSheet("font-size: 22px;")
        l.addWidget(self._mode_title)

        cards_row = QHBoxLayout()
        cards_row.setSpacing(14)

        self._local_card = ModeCard(
            icon="🔒",
            title=tr("onb.mode.local"),
            subtitle=tr("onb.mode.local.subtitle"),
            body=tr("onb.mode.local.body"),
            badge=tr("onb.mode.local.badge"),
        )
        self._local_card.clicked.connect(lambda: self._pick_mode("local"))
        cards_row.addWidget(self._local_card)

        self._cloud_card = ModeCard(
            icon="☁",
            title=tr("onb.mode.cloud"),
            subtitle=tr("onb.mode.cloud.subtitle"),
            body=tr("onb.mode.cloud.body"),
            badge=tr("onb.mode.cloud.badge"),
        )
        self._cloud_card.clicked.connect(lambda: self._pick_mode("cloud"))
        cards_row.addWidget(self._cloud_card)

        l.addLayout(cards_row)
        l.addStretch()

        # Initial highlight reflects current pick
        self._sync_mode_cards()
        return page

    def _build_auto_mode(self) -> QWidget:
        """Auto-Mode page — interactive demo with 4 clickable context tabs.
        Same dictation, four tonalities (Prompt / Email / Slack / Formal).
        Toggle at the bottom flips cleanup_auto_mode + cleanup_enabled."""
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 14, 0, 14)
        l.setSpacing(12)

        self._auto_title = QLabel("Auto-Mode — die Tonalität trifft sich selbst")
        self._auto_title.setObjectName("h1")
        self._auto_title.setStyleSheet("font-size: 22px;")
        l.addWidget(self._auto_title)

        self._auto_sub = QLabel(
            "Sonar merkt, in welchem Fenster du tippst, und passt den "
            "Cleanup-Stil automatisch an. Diktierst du in ChatGPT? Du bekommst "
            "einen strukturierten Prompt. In Gmail? Eine höfliche Mail. In "
            "Slack? Ein lockerer Ping. Klick dich durch:"
        )
        self._auto_sub.setObjectName("dim")
        self._auto_sub.setWordWrap(True)
        l.addWidget(self._auto_sub)

        # Tab row
        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self._auto_tabs: dict[str, QPushButton] = {}
        for key, icon, label in [
            ("chatgpt", "🤖", "ChatGPT"),
            ("email", "✉", "Gmail"),
            ("slack", "💬", "Slack"),
            ("word", "📄", "Word"),
        ]:
            btn = QPushButton(f"  {icon}  {label}")
            btn.setCheckable(True)
            btn.clicked.connect(lambda _checked, k=key: self._on_auto_tab(k))
            btn.setStyleSheet(
                "QPushButton {"
                "  background: rgba(15, 23, 42, 0.55);"
                "  color: #cbd5e1;"
                "  border: 1px solid rgba(148, 163, 184, 0.18);"
                "  border-radius: 10px;"
                "  padding: 9px 16px;"
                "  font-weight: 600;"
                "}"
                "QPushButton:hover {"
                "  border-color: rgba(34, 211, 238, 0.45);"
                "  color: #fff;"
                "}"
                "QPushButton:checked {"
                "  background: #22d3ee;"
                "  color: #030b18;"
                "  border-color: #22d3ee;"
                "}"
            )
            tabs.addWidget(btn)
            self._auto_tabs[key] = btn
        tabs.addStretch()
        l.addLayout(tabs)

        # Output panel — single QLabel that re-renders on tab click
        from PyQt6.QtWidgets import QFrame
        self._auto_panel = QFrame()
        self._auto_panel.setStyleSheet(
            "QFrame {"
            "  background: rgba(3, 11, 24, 0.55);"
            "  border: 1px solid rgba(34, 211, 238, 0.18);"
            "  border-radius: 12px;"
            "  padding: 16px;"
            "}"
        )
        panel_l = QVBoxLayout(self._auto_panel)
        panel_l.setContentsMargins(14, 14, 14, 14)
        panel_l.setSpacing(8)

        self._auto_panel_eyebrow = QLabel("CLEANUP-STIL · AUTO-GEWÄHLT  →  Prompt")
        self._auto_panel_eyebrow.setStyleSheet(
            "color: #67e8f9; font-family: ui-monospace, Menlo, monospace; "
            "font-size: 10px; letter-spacing: 2px; font-weight: 700;"
        )
        panel_l.addWidget(self._auto_panel_eyebrow)

        self._auto_panel_text = QLabel("")
        self._auto_panel_text.setWordWrap(True)
        self._auto_panel_text.setStyleSheet(
            "color: #e2e8f0; font-size: 13px; line-height: 1.55;"
            "padding: 4px 2px;"
        )
        self._auto_panel_text.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        panel_l.addWidget(self._auto_panel_text)

        self._auto_panel_note = QLabel("")
        self._auto_panel_note.setWordWrap(True)
        self._auto_panel_note.setObjectName("dim")
        self._auto_panel_note.setStyleSheet(
            "color: #64748b; font-size: 11px; padding-top: 6px;"
        )
        panel_l.addWidget(self._auto_panel_note)

        l.addWidget(self._auto_panel, 1)

        # Toggle row at the bottom
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(12)
        self._auto_toggle_btn = QPushButton("✓ Auto-Mode aktivieren")
        self._auto_toggle_btn.setCheckable(True)
        self._auto_toggle_btn.setChecked(self._working["cleanup_auto_mode"])
        self._auto_toggle_btn.toggled.connect(self._on_auto_toggle)
        self._auto_toggle_btn.setStyleSheet(
            "QPushButton {"
            "  background: rgba(34, 211, 238, 0.15);"
            "  color: #22d3ee;"
            "  border: 1px solid rgba(34, 211, 238, 0.4);"
            "  border-radius: 10px;"
            "  padding: 10px 18px;"
            "  font-weight: 700;"
            "}"
            "QPushButton:checked {"
            "  background: #22d3ee;"
            "  color: #030b18;"
            "  border-color: #22d3ee;"
            "}"
            "QPushButton:hover {"
            "  border-color: #67e8f9;"
            "}"
        )
        toggle_row.addWidget(self._auto_toggle_btn)

        self._auto_toggle_hint = QLabel(
            "Default-Style aus Settings greift wenn keine Regel matched."
        )
        self._auto_toggle_hint.setObjectName("dim")
        self._auto_toggle_hint.setStyleSheet("color: #64748b; font-size: 11px;")
        toggle_row.addWidget(self._auto_toggle_hint)
        toggle_row.addStretch()
        l.addLayout(toggle_row)

        # Initial render
        self._on_auto_tab("chatgpt")
        return page

    def _on_auto_tab(self, key: str) -> None:
        # Toggle the right button checked + render the right copy
        for k, btn in self._auto_tabs.items():
            btn.setChecked(k == key)
        ctx = _AUTO_DEMO[key]
        self._auto_panel_eyebrow.setText(
            f"CLEANUP-STIL · AUTO-GEWÄHLT  →  {ctx['style']}"
        )
        self._auto_panel_text.setText(ctx["output"])
        self._auto_panel_note.setText(ctx["note"])

    def _on_auto_toggle(self, checked: bool) -> None:
        self._working["cleanup_auto_mode"] = checked
        # If user opted in, flip cleanup_enabled too — auto-mode is
        # meaningless without cleanup. They can still turn it off later.
        if checked:
            self._working["cleanup_enabled"] = True
        self._auto_toggle_btn.setText(
            "✓ Auto-Mode ist an" if checked else "Auto-Mode aktivieren"
        )

    def _build_test(self) -> QWidget:
        page = QWidget()
        l = QVBoxLayout(page)
        l.setContentsMargins(0, 14, 0, 14)
        l.setSpacing(12)

        self._test_title = QLabel(tr("onb.test.title"))
        self._test_title.setObjectName("h1")
        self._test_title.setStyleSheet("font-size: 22px;")
        l.addWidget(self._test_title)

        self._test_sub = QLabel(tr("onb.test.sub"))
        self._test_sub.setObjectName("dim")
        self._test_sub.setWordWrap(True)
        l.addWidget(self._test_sub)

        # Hotkey reminder so user knows what to press
        hotkey_row = QHBoxLayout()
        hotkey_row.setSpacing(10)
        self._test_hotkey_lbl = QLabel(tr("onb.test.your_hotkey"))
        self._test_hotkey_lbl.setObjectName("dim")
        hotkey_row.addWidget(self._test_hotkey_lbl)
        self._test_key_viz = KeyVisualizer(self._working["hotkey"])
        hotkey_row.addWidget(self._test_key_viz)
        hotkey_row.addStretch()
        l.addLayout(hotkey_row)

        # Mic-level meter
        l.addSpacing(4)
        self._test_mic_lbl = QLabel(tr("onb.test.mic_label"))
        l.addWidget(self._test_mic_lbl)
        self._meter = MicLevelMeter()
        l.addWidget(self._meter)

        # Try-it-out dummy input field — user can focus it + dictate
        l.addSpacing(4)
        self._test_try_lbl = QLabel(tr("onb.test.try_label"))
        l.addWidget(self._test_try_lbl)
        self._try_field = QTextEdit()
        self._try_field.setPlaceholderText(tr("onb.test.try_placeholder"))
        self._try_field.setStyleSheet(
            f"QTextEdit {{ background: {NIGHT_2}; color: {WHITE}; "
            f"border: 1px solid {NIGHT_BORDER}; border-radius: 8px; padding: 10px; }} "
            f"QTextEdit:focus {{ border-color: {CYAN}; }}"
        )
        self._try_field.setMinimumHeight(80)
        self._try_field.setMaximumHeight(120)
        l.addWidget(self._try_field)

        l.addSpacing(4)
        self._test_ready = QLabel(tr("onb.test.ready"))
        self._test_ready.setStyleSheet(f"color: {CYAN}; font-size: 13px; font-weight: 600;")
        self._test_ready.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.addWidget(self._test_ready)
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

    # ── Theme picker ───────────────────────────────────────────────────────

    def _pick_theme(self, theme_name: str) -> None:
        self._working["ui_theme"] = theme_name
        self._sync_theme_cards()
        # Apply live so the user sees what they're getting before clicking Next
        try:
            from PyQt6.QtWidgets import QApplication
            from .. import theme as _theme
            _theme.apply(QApplication.instance(), theme_name)
        except Exception:
            pass

    def _sync_theme_cards(self) -> None:
        is_dark = self._working.get("ui_theme", "dark") == "dark"
        self._theme_dark_card.set_active(is_dark)
        self._theme_light_card.set_active(not is_dark)

    # ── Account sign-up (email-verified, v0.5.0) ──────────────────────────

    def _on_account_primary(self) -> None:
        """Primary button click.  Dispatches based on stage:
        - "email" → request a verification code
        - "code" → verify the entered code"""
        if self._acc_stage == "email":
            self._do_request_code()
        elif self._acc_stage == "code":
            self._do_verify_code()

    def _on_account_resend(self) -> None:
        """User asked for a fresh code.  Reuses request_code with the
        already-entered email; the server enforces the 30 s cooldown."""
        if self._acc_stage != "code":
            return
        self._do_request_code(resending=True)

    def _do_request_code(self, resending: bool = False) -> None:
        email = (self._acc_email.text() or "").strip()
        if "@" not in email or "." not in email or len(email) < 5:
            self._set_account_status(tr("onb.account.invalid"), error=True)
            return
        self._acc_signup_btn.setEnabled(False)
        self._acc_email.setEnabled(False)
        if resending:
            self._acc_resend_btn.setEnabled(False)
            self._set_account_status(tr("onb.account.resending"), error=False)
        else:
            self._set_account_status(tr("onb.account.requesting"), error=False)

        from PyQt6.QtCore import QThread, pyqtSignal as _sig
        from .. import account as _account_api

        class _RequestCodeWorker(QObject):
            done = _sig(dict)         # ttl_seconds + resend_cooldown
            failed = _sig(str, int)   # (kind, retry_after)

            def __init__(self, endpoint: str, email_: str) -> None:
                super().__init__()
                self.endpoint = endpoint
                self.email = email_

            def run(self) -> None:
                try:
                    res = _account_api.request_code(self.endpoint, self.email)
                    self.done.emit({
                        "ttl": res.ttl_seconds,
                        "cooldown": res.resend_cooldown_seconds,
                    })
                except _account_api.EmailAlreadyRegistered:
                    self.failed.emit("exists", 0)
                except _account_api.CodeRateLimited as e:
                    self.failed.emit("rate_limited", int(e.retry_after))
                except _account_api.EmailDeliveryFailed:
                    self.failed.emit("delivery", 0)
                except _account_api.CodeError as e:
                    self.failed.emit(f"err:{e}", 0)
                except Exception as e:  # noqa: BLE001
                    self.failed.emit(f"unknown:{e}", 0)

        endpoint = self.config.subunit_endpoint or ""
        thread = QThread(self)
        worker = _RequestCodeWorker(endpoint, email)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_request_code_ok)
        worker.failed.connect(self._on_request_code_fail)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._signup_thread = (thread, worker)
        thread.start()

    def _on_request_code_ok(self, result: dict) -> None:
        """Server emailed the code.  Switch the UI into stage 2."""
        self._acc_stage = "code"
        self._acc_email.setEnabled(False)  # locked while we verify
        self._acc_code_lbl.setVisible(True)
        self._acc_code.setVisible(True)
        self._acc_resend_btn.setVisible(True)
        self._acc_resend_btn.setEnabled(True)
        self._acc_signup_btn.setEnabled(True)
        self._acc_signup_btn.setText(tr("onb.account.btn.verify"))
        self._set_account_status(tr("onb.account.code_sent"), error=False)
        self._acc_code.setFocus()

    def _on_request_code_fail(self, kind: str, retry_after: int) -> None:
        self._acc_signup_btn.setEnabled(True)
        self._acc_email.setEnabled(True)
        if self._acc_stage == "code":
            self._acc_resend_btn.setEnabled(True)

        if kind == "exists":
            self._set_account_status(tr("onb.account.exists"), error=True)
        elif kind == "rate_limited":
            self._set_account_status(
                tr("onb.account.rate_limited", n=retry_after), error=True
            )
        elif kind == "delivery":
            self._set_account_status(tr("onb.account.delivery_failed"), error=True)
        else:
            self._set_account_status(tr("onb.account.network"), error=True)

    def _do_verify_code(self) -> None:
        email = (self._acc_email.text() or "").strip()
        code = (self._acc_code.text() or "").strip()
        if not code.isdigit() or len(code) != 6:
            self._set_account_status(tr("onb.account.code_invalid"), error=True)
            return
        self._acc_signup_btn.setEnabled(False)
        self._acc_code.setEnabled(False)
        self._set_account_status(tr("onb.account.verifying"), error=False)

        from PyQt6.QtCore import QThread, pyqtSignal as _sig
        from .. import account as _account_api

        class _VerifyCodeWorker(QObject):
            done = _sig(dict)
            failed = _sig(str, int)  # (kind, attempts_remaining)

            def __init__(self, endpoint: str, email_: str, code_: str) -> None:
                super().__init__()
                self.endpoint = endpoint
                self.email = email_
                self.code = code_

            def run(self) -> None:
                try:
                    acct = _account_api.verify_code(
                        self.endpoint, self.email, self.code
                    )
                    self.done.emit({
                        "email": acct.email,
                        "api_key": acct.api_key,
                        "plan": acct.plan,
                    })
                except _account_api.CodeWrong as e:
                    self.failed.emit("wrong", int(e.attempts_remaining))
                except _account_api.CodeExpired:
                    self.failed.emit("expired", 0)
                except _account_api.CodeLocked:
                    self.failed.emit("locked", 0)
                except _account_api.CodeNotFound:
                    self.failed.emit("not_found", 0)
                except _account_api.EmailAlreadyRegistered:
                    self.failed.emit("exists", 0)
                except _account_api.CodeError as e:
                    self.failed.emit(f"err:{e}", 0)
                except Exception as e:  # noqa: BLE001
                    self.failed.emit(f"unknown:{e}", 0)

        endpoint = self.config.subunit_endpoint or ""
        thread = QThread(self)
        worker = _VerifyCodeWorker(endpoint, email, code)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_signup_ok)
        worker.failed.connect(self._on_verify_fail)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._signup_thread = (thread, worker)
        thread.start()

    def _on_verify_fail(self, kind: str, attempts_remaining: int) -> None:
        self._acc_signup_btn.setEnabled(True)
        self._acc_code.setEnabled(True)
        self._acc_code.setFocus()
        self._acc_code.selectAll()

        if kind == "wrong":
            if attempts_remaining > 0:
                self._set_account_status(
                    tr("onb.account.code_wrong", n=attempts_remaining), error=True
                )
            else:
                self._set_account_status(tr("onb.account.locked"), error=True)
        elif kind == "expired":
            self._set_account_status(tr("onb.account.code_expired"), error=True)
        elif kind == "locked":
            self._set_account_status(tr("onb.account.locked"), error=True)
        elif kind == "not_found":
            self._set_account_status(tr("onb.account.code_not_found"), error=True)
        elif kind == "exists":
            self._set_account_status(tr("onb.account.exists"), error=True)
        else:
            self._set_account_status(tr("onb.account.network"), error=True)

    def _on_signup_ok(self, result: dict) -> None:
        # Persist email + key + plan + trial start time
        import time as _t
        self._working["account_email"] = result.get("email", "")
        self._working["subunit_api_key"] = result.get("api_key", "")
        # Server returns plan='free' on creation — locally we flip to 'trial'
        # so the desktop app shows the 7-day countdown until v0.3.22 ships
        # the proper /v1/account/subscribe + Stripe flow.
        self._working["plan"] = "trial"
        self._working["trial_started_at"] = int(_t.time())
        # Default the user into Cloud mode after a successful signup —
        # otherwise they paid the email step and stay on Local, which is
        # the wrong default for someone who just opted into Subunit-Cloud.
        self._working["mode"] = "subunit"
        self._set_account_status(tr("onb.account.success"), error=False)
        # Auto-advance after a short beat so they see the confirmation
        QTimer.singleShot(900, lambda: self._sync_step(self.stack.currentIndex() + 1))

    def _on_signup_fail(self, code: str) -> None:
        self._acc_signup_btn.setEnabled(True)
        self._acc_email.setEnabled(True)
        if code == "exists":
            self._set_account_status(tr("onb.account.exists"), error=True)
        elif code == "network":
            self._set_account_status(tr("onb.account.network_error"), error=True)
        else:
            self._set_account_status(f"⚠ {code}", error=True)

    def _set_account_status(self, msg: str, error: bool) -> None:
        self._acc_status.setText(msg)
        color = "#f87171" if error else "#34d399"
        self._acc_status.setStyleSheet(f"color: {color}; padding: 4px 0;")

    def _on_account_skip(self) -> None:
        # Mark explicitly skipped — no email, plan stays free, just advance
        self._working["account_email"] = ""
        self._working["plan"] = "free"
        self._sync_step(self.stack.currentIndex() + 1)

    # ── Nav ────────────────────────────────────────────────────────────────

    def _sync_step(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for i, d in enumerate(self._dots):
            d.set_state(reached=(i <= idx), active=(i == idx))
        self.back_btn.setVisible(idx > 0)
        is_last = idx == self.stack.count() - 1
        self.next_btn.setText(tr("onb.btn.finish") if is_last else tr("onb.btn.next"))
        self.skip_btn.setVisible(not is_last)
        # Trigger the welcome cascade-fade when entering page 0
        if idx == 0:
            QTimer.singleShot(120, self._animate_welcome_in)
        # Account page (idx 1) — only step where the user can hit a server
        # so make sure the email field has focus on entry for fast typing
        if idx == 1 and hasattr(self, "_acc_email"):
            self._acc_email.setFocus()

    def _go_next(self) -> None:
        idx = self.stack.currentIndex()
        # Persist the hotkey choice when leaving the Hotkey step (page 3 in
        # the new 6-step flow: Welcome → Account → Theme → Hotkey → Mode → Test)
        if idx == 3:
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

    # ── Live-key detection (Hotkey page) ───────────────────────────────────

    def keyPressEvent(self, e) -> None:
        token = _qt_key_to_token(e)
        if token:
            self._pressed_keys.add(token)
            if hasattr(self, "_key_viz"):
                self._key_viz.set_pressed(self._pressed_keys)
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:
        token = _qt_key_to_token(e)
        if token and token in self._pressed_keys:
            self._pressed_keys.discard(token)
            if hasattr(self, "_key_viz"):
                self._key_viz.set_pressed(self._pressed_keys)
        super().keyReleaseEvent(e)

    # ── Language toggle ────────────────────────────────────────────────────

    def _on_language_change(self, lang: str) -> None:
        self._working["ui_language"] = lang
        i18n.set_language(lang)
        # Re-render every visible string
        self._refresh_strings()

    def _refresh_strings(self) -> None:
        """Re-pull every string from the i18n bundle. Cheaper than
        rebuilding the whole stack since we keep the widget refs."""
        self.h1_lbl.setText(tr("onb.welcome.title"))
        self.h1_sub.setText(tr("onb.welcome.sub"))
        self.back_btn.setText(tr("onb.btn.back"))
        self.skip_btn.setText(tr("onb.btn.skip"))
        # Next/Finish depends on current page
        is_last = self.stack.currentIndex() == self.stack.count() - 1
        self.next_btn.setText(tr("onb.btn.finish") if is_last else tr("onb.btn.next"))

        # Account page
        if hasattr(self, "_acc_title"):
            self._acc_title.setText(tr("onb.account.title"))
            self._acc_sub.setText(tr("onb.account.sub"))
            self._acc_email_lbl.setText(tr("onb.account.email_label"))
            self._acc_email.setPlaceholderText(tr("onb.account.email_placeholder"))
            self._acc_signup_btn.setText(tr("onb.account.btn.signup"))
            self._acc_skip_btn.setText(tr("onb.account.btn.skip"))
            for key, lbl in getattr(self, "_acc_benefits", []):
                lbl.setText(tr(key))
        # Theme page
        if hasattr(self, "_theme_title"):
            self._theme_title.setText(tr("onb.theme.title"))
            self._theme_sub.setText(tr("onb.theme.sub"))
            self._theme_dark_card.set_strings(
                title=tr("onb.theme.dark"),
                subtitle="",
                body=tr("onb.theme.dark.body"),
                badge="Default",
            )
            self._theme_light_card.set_strings(
                title=tr("onb.theme.light"),
                subtitle="",
                body=tr("onb.theme.light.body"),
                badge="",
            )

        # Hotkey page
        if hasattr(self, "_hotkey_title"):
            self._hotkey_title.setText(tr("onb.hotkey.title"))
            self._hotkey_sub.setText(tr("onb.hotkey.sub"))
            self._hotkey_hint.setText(tr("onb.hotkey.hint"))
            self._hotkey_live_hint.setText(tr("onb.hotkey.live_hint"))
        # Page 2 (Mode)
        if hasattr(self, "_mode_title"):
            self._mode_title.setText(tr("onb.mode.title"))
            self._local_card.set_strings(
                title=tr("onb.mode.local"),
                subtitle=tr("onb.mode.local.subtitle"),
                body=tr("onb.mode.local.body"),
                badge=tr("onb.mode.local.badge"),
            )
            self._cloud_card.set_strings(
                title=tr("onb.mode.cloud"),
                subtitle=tr("onb.mode.cloud.subtitle"),
                body=tr("onb.mode.cloud.body"),
                badge=tr("onb.mode.cloud.badge"),
            )
        # Page 3 (Test)
        if hasattr(self, "_test_title"):
            self._test_title.setText(tr("onb.test.title"))
            self._test_sub.setText(tr("onb.test.sub"))
            self._test_mic_lbl.setText(tr("onb.test.mic_label"))
            self._test_try_lbl.setText(tr("onb.test.try_label"))
            self._test_hotkey_lbl.setText(tr("onb.test.your_hotkey"))
            self._try_field.setPlaceholderText(tr("onb.test.try_placeholder"))
            self._test_ready.setText(tr("onb.test.ready"))
        # Welcome features (have fixed indexes — cheaper to rebuild)
        # The feature rows aren't easily re-bindable, so skip for now;
        # toggling language usually happens before the user progresses
        # past page 0, and we re-build the page each launch.


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
        self._tokens: list[str] = []
        self._pressed: set[str] = set()
        self.set_combo(combo)
        self.setMinimumHeight(self.KEY_HEIGHT + 10)

    def set_combo(self, combo: str) -> None:
        # Parse "<ctrl>+<shift>+<space>" → tokens + pretty labels
        raw = combo.replace("<", "").replace(">", "").split("+")
        tokens = []
        pretty = []
        for k in raw:
            k = k.strip().lower()
            if not k:
                continue
            tokens.append(k)
            label = {
                "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "cmd": "⌘",
                "super": "⊞", "space": "Space", "tab": "Tab", "enter": "Enter",
                "esc": "Esc",
            }.get(k, k.upper() if len(k) == 1 else k.capitalize())
            pretty.append(label)
        self._keys = pretty
        self._tokens = tokens
        self.updateGeometry()
        self.update()

    def set_pressed(self, pressed: set[str]) -> None:
        """Update which keys should render in the "live pressed" highlight.
        Only keys that are part of the current combo light up — random
        keypresses are ignored to avoid noise."""
        self._pressed = set(pressed)
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

        all_pressed = (
            len(self._tokens) > 0
            and all(tok in self._pressed for tok in self._tokens)
        )

        x = 0
        for i, (k, tok) in enumerate(zip(self._keys, self._tokens)):
            w = max(60, fm.horizontalAdvance(k) + self.KEY_PADDING * 2)
            y = 3
            is_pressed = tok in self._pressed
            # Glow halo — stronger when pressed, even stronger when whole
            # combo is held down.
            if all_pressed:
                halo_alpha = 90
            elif is_pressed:
                halo_alpha = 70
            else:
                halo_alpha = 28
            halo = QColor(CYAN)
            halo.setAlpha(halo_alpha)
            p.setBrush(halo)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(x - 4, y - 4, w + 8, self.KEY_HEIGHT + 8, 12, 12)
            # Key body — fills with cyan when pressed
            if is_pressed:
                grad = QLinearGradient(0, y, 0, y + self.KEY_HEIGHT)
                grad.setColorAt(0.0, QColor("#6cdfff"))
                grad.setColorAt(1.0, QColor(CYAN))
                p.setBrush(grad)
                p.setPen(QPen(QColor("#6cdfff"), 1.6))
            else:
                grad = QLinearGradient(0, y, 0, y + self.KEY_HEIGHT)
                grad.setColorAt(0.0, QColor("#1c3a52"))
                grad.setColorAt(1.0, QColor(NIGHT_2))
                p.setBrush(grad)
                p.setPen(QPen(QColor(NIGHT_BORDER), 1.4))
            p.drawRoundedRect(x, y, w, self.KEY_HEIGHT, 10, 10)
            # Highlight stripe — only on idle keys
            if not is_pressed:
                p.setPen(QPen(QColor(CYAN), 2.0))
                p.drawLine(x + 8, y + 4, x + w - 8, y + 4)
            # Text
            p.setPen(QColor(NIGHT) if is_pressed else QColor(WHITE))
            p.drawText(
                x, y, w, self.KEY_HEIGHT,
                int(Qt.AlignmentFlag.AlignCenter),
                k,
            )

            x += w
            if i < len(self._keys) - 1:
                # "+" separator — turns cyan when both sides pressed
                left_pressed = tok in self._pressed
                right_tok = self._tokens[i + 1] if i + 1 < len(self._tokens) else None
                right_pressed = right_tok in self._pressed if right_tok else False
                p.setPen(QColor(CYAN) if (left_pressed and right_pressed) else QColor(WHITE_DIM))
                p.drawText(x, y, 18, self.KEY_HEIGHT,
                           int(Qt.AlignmentFlag.AlignCenter), "+")
                x += 18

        # Confirmation marker on the right when the whole combo is down
        if all_pressed:
            cx_check = x + 8
            cy_check = self.KEY_HEIGHT // 2 + 3
            p.setPen(QPen(QColor("#22c55e"), 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawLine(cx_check - 6, cy_check, cx_check - 1, cy_check + 5)
            p.drawLine(cx_check - 1, cy_check + 5, cx_check + 7, cy_check - 5)


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

    def set_strings(
        self,
        title: str,
        subtitle: str,
        body: str,
        badge: str,
    ) -> None:
        """Re-bind labels for live language switching."""
        self._title = title
        self._subtitle = subtitle
        self._body = body
        self._badge = badge
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


# v0.3.26: Auto-Mode demo data — same dictation rendered four ways.
# Mirrors the marketing site's AutoMode.tsx so the user sees the same
# examples in both places. The transcribed-input is the same German
# spoken sentence; the four outputs come from running it through the
# four cleanup styles (prompt / email / slack / formal).
_AUTO_DEMO: dict[str, dict[str, str]] = {
    "chatgpt": {
        "style": "Prompt",
        "output": (
            "Schreibe eine Nachricht an Maria, die das morgige 10-Uhr-"
            "Meeting verschiebt.\n\n"
            "• Empfänger: Maria\n"
            "• Begründung: Ich brauche noch Unterlagen.\n"
            "• Tonfall: höflich, knapp.\n"
            "• Sprache: Deutsch"
        ),
        "note": "Strukturiert für AI-Agents — Goal + Bullets + Constraints.",
    },
    "email": {
        "style": "Email",
        "output": (
            "Hi Maria,\n\n"
            "ich muss unser Meeting morgen um 10 Uhr leider verschieben — "
            "mir fehlen noch ein paar Unterlagen.\n\n"
            "Ich melde mich gleich mit einem neuen Vorschlag.\n\n"
            "Beste Grüße"
        ),
        "note": "Höfliche Mail — Anrede + Body + Closer.",
    },
    "slack": {
        "style": "Slack",
        "output": (
            "Maria, sorry — kurz zwischendurch: Meeting morgen 10 Uhr muss "
            "ich verschieben, mir fehlen noch Unterlagen. Schick dir gleich "
            "einen neuen Slot ✌"
        ),
        "note": "Casual chat — keine Anrede / kein Sign-off.",
    },
    "word": {
        "style": "Formal",
        "output": (
            "Sehr geehrte Frau Maria,\n\n"
            "leider muss ich unser für morgen, 10:00 Uhr terminiertes "
            "Meeting verschieben. Mir fehlen noch einige Unterlagen, "
            "die ich vorab benötige, um das Gespräch produktiv führen "
            "zu können.\n\n"
            "Ich werde Ihnen umgehend einen alternativen Termin "
            "vorschlagen.\n\n"
            "Mit freundlichen Grüßen"
        ),
        "note": "Business-formal — komplette Anrede + ausführliche Begründung.",
    },
}
