"""Main application window — dashboard / control center.

A real top-level window so Sonar feels like an installed app, not just a
tray gadget. Closing the window hides it; quitting goes through the tray menu.
"""
from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from .. import hardware as _hw
from ..config import Config
from ..transcriber import ALL_MODES, CLOUD_MODES, mode_label
from .widgets import AnimatedToggle, BrandLogo

LOCAL_MODELS = ["base", "small", "medium", "large-v3"]

CYAN = "#40d6ff"
NIGHT = "#020817"
NIGHT_2 = "#0c1828"
NIGHT_BORDER = "#1f3145"
WHITE = "#e6f2fb"
WHITE_DIM = "#9fb1bd"

QSS = f"""
QMainWindow, QWidget#central {{
    background: {NIGHT};
    color: {WHITE};
}}
QLabel {{ color: {WHITE}; }}
QLabel#dim {{ color: {WHITE_DIM}; }}
QLabel#h1 {{ font-size: 22px; font-weight: 600; }}
QLabel#h2 {{ font-size: 14px; font-weight: 500; color: {WHITE_DIM}; letter-spacing: 1px; }}
QLabel#big {{ font-size: 28px; font-weight: 600; color: {CYAN}; }}
QLabel#statusBig {{ font-size: 16px; font-weight: 500; }}
QFrame#card {{
    background: {NIGHT_2};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 14px;
}}
QPushButton {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 8px;
    padding: 8px 14px;
    min-width: 100px;
}}
QPushButton:hover {{ border-color: {CYAN}; }}
QPushButton#primary {{
    background: {CYAN};
    color: {NIGHT};
    border: none;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #6cdfff; }}
QComboBox {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 6px;
    padding: 4px 8px;
    min-width: 220px;
}}
QListWidget {{
    background: {NIGHT_2};
    color: {WHITE};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 10px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 8px 10px;
    border-bottom: 1px solid {NIGHT_BORDER};
}}
QListWidget::item:selected {{
    background: #143246;
    color: {WHITE};
}}
/* 2026-05-16: recent-list lives inside a card frame, so kill its own
   border/background and let the card chrome show through. */
QListWidget#recentList {{
    background: transparent;
    border: none;
    padding: 0;
}}
QListWidget#recentList::item {{
    padding: 10px 6px;
    border-bottom: 1px solid {NIGHT_BORDER};
}}
QListWidget#recentList::item:last-child {{
    border-bottom: none;
}}
/* 2026-05-16: Quality / Fast segmented pills inside the Cloud detail card. */
QPushButton#qualityPill {{
    background: transparent;
    color: {WHITE_DIM};
    border: 1px solid {NIGHT_BORDER};
    border-radius: 6px;
    padding: 4px 12px;
    min-width: 78px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 1px;
}}
QPushButton#qualityPillActive {{
    background: {CYAN};
    color: {NIGHT};
    border: 1px solid {CYAN};
    border-radius: 6px;
    padding: 4px 12px;
    min-width: 78px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QPushButton#qualityPill:hover {{ border-color: {CYAN}; color: {WHITE}; }}
"""


class BigModeSwitch(QWidget):
    """A two-segment privacy switch that's the centerpiece of the home
    screen. Active half is bright cyan with a glowing rim and a giant
    label; inactive half is dim and muted. Clicking either half flips
    the mode. Replaces the small toggle TJ called "altbacken" + the
    confusing dual-dropdown layout below it.
    """

    HEIGHT = 110
    HALF_RADIUS = 22

    def __init__(self, is_local: bool, on_change) -> None:
        super().__init__()
        self._is_local = is_local
        self._on_change = on_change  # callable(checked: bool)
        self._indicator_pos = 0.0  # 0 = left half active, 1 = right half active
        self._indicator_pos = 0.0 if is_local else 1.0
        self._hovered_half = -1  # -1 none, 0 local, 1 cloud

        self.setMinimumHeight(self.HEIGHT)
        self.setMaximumHeight(self.HEIGHT)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"indicator")
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    # Property used by the slide animation
    def get_indicator(self) -> float:
        return self._indicator_pos

    def set_indicator(self, v: float) -> None:
        self._indicator_pos = v
        self.update()

    indicator = pyqtProperty(float, get_indicator, set_indicator)

    def set_local(self, is_local: bool) -> None:
        if self._is_local == is_local:
            return
        self._is_local = is_local
        self._anim.stop()
        self._anim.setStartValue(self._indicator_pos)
        self._anim.setEndValue(0.0 if is_local else 1.0)
        self._anim.start()

    def is_local(self) -> bool:
        return self._is_local

    # ── mouse ──────────────────────────────────────────────────────────────

    def mousePressEvent(self, e) -> None:
        half_w = self.width() / 2
        new_local = e.position().x() < half_w
        if new_local != self._is_local:
            self.set_local(new_local)
            self._on_change(new_local)

    def mouseMoveEvent(self, e) -> None:
        half_w = self.width() / 2
        h = 0 if e.position().x() < half_w else 1
        if h != self._hovered_half:
            self._hovered_half = h
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, _e) -> None:
        if self._hovered_half != -1:
            self._hovered_half = -1
            self.update()

    # ── paint ──────────────────────────────────────────────────────────────

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect().adjusted(2, 2, -2, -2)

        # Outer card
        path = QPainterPath()
        path.addRoundedRect(rect.toRectF(), self.HALF_RADIUS, self.HALF_RADIUS)
        p.fillPath(path, QColor(NIGHT_2))
        p.setPen(QPen(QColor(NIGHT_BORDER), 1.0))
        p.drawPath(path)

        # Sliding active-indicator pill
        half_w = rect.width() / 2
        ind_x = rect.x() + 6 + int(self._indicator_pos * (half_w - 6))
        ind_w = int(half_w - 12)
        ind_rect = QRect(ind_x, rect.y() + 6, ind_w, rect.height() - 12)
        ind_color = QColor(CYAN) if self._is_local else QColor("#5b8fb6")
        # active-side glow halo behind the pill
        for r in range(8, 0, -2):
            halo = QColor(ind_color)
            halo.setAlpha(20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawRoundedRect(
                ind_rect.adjusted(-r, -r, r, r),
                self.HALF_RADIUS + r, self.HALF_RADIUS + r,
            )
        p.setBrush(ind_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(ind_rect, self.HALF_RADIUS - 4, self.HALF_RADIUS - 4)

        # Labels
        f_big = QFont()
        f_big.setPointSize(14)
        f_big.setWeight(QFont.Weight.Bold)
        f_sub = QFont()
        f_sub.setPointSize(8)
        f_sub.setWeight(QFont.Weight.Medium)

        for i, (icon, big, sub) in enumerate([
            ("🔒", "LOKAL", "Audio bleibt auf diesem Gerät"),
            ("☁", "CLOUD", "DSGVO • EU-Server • Subunit"),
        ]):
            half_x = rect.x() + i * half_w
            is_active = (i == 0 and self._is_local) or (i == 1 and not self._is_local)
            p.setPen(QColor(NIGHT) if is_active else QColor(WHITE_DIM))
            f_icon = QFont()
            f_icon.setPointSize(20)
            p.setFont(f_icon)
            p.drawText(
                QRect(int(half_x), rect.y() + 14, int(half_w), 26),
                int(Qt.AlignmentFlag.AlignCenter),
                icon,
            )
            p.setFont(f_big)
            p.drawText(
                QRect(int(half_x), rect.y() + 42, int(half_w), 24),
                int(Qt.AlignmentFlag.AlignCenter),
                big,
            )
            p.setPen(QColor(NIGHT) if is_active else QColor(WHITE_DIM))
            p.setFont(f_sub)
            p.drawText(
                QRect(int(half_x), rect.y() + 68, int(half_w), 24),
                int(Qt.AlignmentFlag.AlignCenter),
                sub,
            )


def _format_seconds(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


class MainWindow(QMainWindow):
    def __init__(
        self,
        config: Config,
        on_change_mode: Callable[[str], None],
        on_open_settings: Callable[[], None],
        on_open_history: Callable[[], None],
        on_quit: Callable[[], None],
        on_open_meetings: Callable[[], None] | None = None,
        on_start_meeting: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self._on_change_mode = on_change_mode
        self._on_open_settings = on_open_settings
        self._on_open_history = on_open_history
        self._on_open_meetings = on_open_meetings
        self._on_start_meeting = on_start_meeting
        self._on_quit = on_quit

        self.setWindowTitle("Sonar")
        self.setStyleSheet(QSS)
        # 2026-05-16: TJ flagged the old 720x600 default as cramped. Open
        # at a roomy 1180x820 so all sections have breathing space without
        # going full-screen by default (still resizable, still has a sane
        # minimum). On small displays we fall back to slightly less than
        # the available screen so we never spawn off-screen.
        self.setMinimumSize(880, 640)
        try:
            screen = self.screen() or QApplication.primaryScreen()
            avail = screen.availableGeometry() if screen else None
            if avail and (avail.width() < 1200 or avail.height() < 860):
                w = max(880, int(avail.width() * 0.92))
                h = max(640, int(avail.height() * 0.92))
                self.resize(w, h)
            else:
                self.resize(1180, 820)
        except Exception:
            self.resize(1180, 820)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(32, 26, 32, 26)
        outer.setSpacing(22)

        # ── Header ─────────────────────────────────────────────────────────
        header = QHBoxLayout()
        header.setSpacing(14)
        header.addWidget(BrandLogo(size=56))
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("Sonar")
        title.setObjectName("h1")
        version = QLabel(f"v{__version__}")
        version.setObjectName("dim")
        title_box.addWidget(title)
        title_box.addWidget(version)
        header.addLayout(title_box)
        header.addStretch()
        # v0.3.22: plan badge (Trial · 5d left | Pro | Local only). Hidden
        # by default until main.py refreshes /v1/account/info on startup.
        from .plan_badge import PlanBadge
        self.plan_badge = PlanBadge()
        self.plan_badge.hide()
        header.addWidget(self.plan_badge, 0, Qt.AlignmentFlag.AlignTop)
        # Status is now reflected only in the bubble + tray icon. The redundant
        # "● idle" pill in the header looked like a notification badge and TJ
        # wanted it gone.
        self.status_lbl = QLabel("")
        self.status_lbl.setVisible(False)
        outer.addLayout(header)

        # ── Stats row ──────────────────────────────────────────────────────
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        self.stat_count = self._make_stat_card(
            "TRANSCRIBED", str(config.total_transcriptions)
        )
        stats_row.addWidget(self.stat_count)
        self.stat_audio = self._make_stat_card(
            "AUDIO PROCESSED", _format_seconds(config.total_audio_seconds)
        )
        stats_row.addWidget(self.stat_audio)
        self.stat_saved = self._make_stat_card(
            "TIME SAVED",
            _format_seconds(config.total_audio_seconds * 2.5),
        )
        stats_row.addWidget(self.stat_saved)
        outer.addLayout(stats_row)

        # ── BIG Privacy-Hero Switch ────────────────────────────────────────
        # TJ-feedback: the local/cloud choice is THE primary decision. Make
        # it impossible to miss — a giant 2-segment switch with the active
        # half lit up, the inactive one dim. Below it, a single contextual
        # detail card that morphs to show the active mode's settings.
        self.mode_switch = BigModeSwitch(
            is_local=(config.mode == "local"),
            on_change=self._on_local_toggled,
        )
        outer.addWidget(self.mode_switch)

        # Detail card — below the hero. Shows local model when local active,
        # cloud provider when cloud active. Replaces the dual-dropdown layout
        # that confused TJ ("man klickt drauf und das funktioniert nicht").
        hw = _hw.detect()
        recommended = _hw.recommend_local_model(hw)
        self._hw_summary = _hw.describe(hw)
        self._recommended_model = recommended

        self.detail_card = QFrame()
        self.detail_card.setObjectName("card")
        self._detail_layout = QHBoxLayout(self.detail_card)
        self._detail_layout.setContentsMargins(18, 14, 18, 14)
        self._detail_layout.setSpacing(12)
        outer.addWidget(self.detail_card)

        # Hidden combos: kept around for backwards-compat with any code that
        # still calls refresh_mode(); the user interacts with the visible
        # cards above which open compact pickers on click.
        self.local_model_combo = QComboBox()
        for m in LOCAL_MODELS:
            label = m
            if m == recommended:
                label += "  ⭐ recommended for your hardware"
            self.local_model_combo.addItem(label, m)
        idx = self.local_model_combo.findData(config.local_model)
        if idx >= 0:
            self.local_model_combo.setCurrentIndex(idx)
        self.local_model_combo.currentIndexChanged.connect(self._on_local_model_changed)
        self.local_model_combo.hide()

        self.mode_combo = QComboBox()
        for mode_id in CLOUD_MODES:
            label = mode_label(mode_id)
            if mode_id == "subunit":
                label += "  ·  Recommended"
            self.mode_combo.addItem(label, mode_id)
        cloud_mode = config.mode if config.mode in CLOUD_MODES else config.last_cloud_mode
        idx = self.mode_combo.findData(cloud_mode)
        if idx >= 0:
            self.mode_combo.setCurrentIndex(idx)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.mode_combo.hide()

        # Hotkey label sits below the detail card, dim, single-line
        hotkey_row = QHBoxLayout()
        hotkey_row.setContentsMargins(4, 0, 4, 0)
        hotkey_row.addStretch()
        self.hotkey_lbl = QLabel(f"Hotkey: {config.hotkey}")
        self.hotkey_lbl.setObjectName("dim")
        hotkey_row.addWidget(self.hotkey_lbl)
        outer.addLayout(hotkey_row)

        self._refresh_detail_card()

        # ── Recent transcriptions ──────────────────────────────────────────
        # 2026-05-16: wrap the title + list in a real card so the section
        # stops competing with the controls above. The list expands to
        # fill remaining vertical space — in the new 1180x820 layout this
        # is the dominant region by design.
        recent_card = QFrame()
        recent_card.setObjectName("card")
        recent_box = QVBoxLayout(recent_card)
        recent_box.setContentsMargins(18, 14, 18, 14)
        recent_box.setSpacing(10)

        recent_header = QHBoxLayout()
        recent_title = QLabel("RECENT TRANSCRIPTIONS")
        recent_title.setObjectName("h2")
        recent_header.addWidget(recent_title)
        recent_header.addStretch()
        recent_box.addLayout(recent_header)

        self.history_list = QListWidget()
        self.history_list.setObjectName("recentList")
        self.history_list.setSelectionMode(self.history_list.SelectionMode.SingleSelection)
        self.history_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.history_list.setFrameShape(QFrame.Shape.NoFrame)
        recent_box.addWidget(self.history_list, 1)
        outer.addWidget(recent_card, 1)

        # ── Footer ─────────────────────────────────────────────────────────
        footer = QHBoxLayout()
        footer.setSpacing(10)
        history_btn = QPushButton("Full history…")
        history_btn.clicked.connect(lambda: self._on_open_history())
        meetings_btn = QPushButton("Meetings…")
        meetings_btn.setToolTip("Browse long-form recordings, extract tasks + decisions.")
        if self._on_open_meetings is not None:
            meetings_btn.clicked.connect(lambda: self._on_open_meetings())
        else:
            meetings_btn.setEnabled(False)
        # v0.9.0: Host a multi-participant meeting via meet.subunit.ai.
        start_meeting_btn = QPushButton("🔴 Meeting starten…")
        start_meeting_btn.setToolTip(
            "QR-Code + 6-Stellen-Code generieren, Teilnehmer checken via "
            "meet.subunit.ai ein. DSGVO-konformer Multi-Sprecher-Modus."
        )
        if self._on_start_meeting is not None:
            start_meeting_btn.clicked.connect(lambda: self._on_start_meeting())
        else:
            start_meeting_btn.setEnabled(False)
        settings_btn = QPushButton("Settings…")
        settings_btn.clicked.connect(lambda: self._on_open_settings())
        hide_btn = QPushButton("Hide to tray")
        hide_btn.clicked.connect(self.hide)
        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(lambda: self._on_quit())
        footer.addWidget(history_btn)
        footer.addWidget(meetings_btn)
        footer.addWidget(start_meeting_btn)
        footer.addWidget(settings_btn)
        footer.addStretch()
        footer.addWidget(hide_btn)
        footer.addWidget(quit_btn)
        outer.addLayout(footer)

        # Periodically refresh stats while window is open
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()
        self.refresh()

    # ── public API ─────────────────────────────────────────────────────────

    def set_status(self, label: str, color: str = CYAN) -> None:
        # No-op kept for callsite compatibility — status now lives in the
        # tray icon + floating bubble only.
        return

    def refresh_mode(self) -> None:
        """Re-sync UI with config (after Settings dialog applied)."""
        is_local = self.config.mode == "local"
        self.mode_switch.set_local(is_local)
        # Hidden combos kept in sync for refresh_mode contract
        target_cloud = self.config.mode if self.config.mode in CLOUD_MODES else self.config.last_cloud_mode
        c_idx = self.mode_combo.findData(target_cloud)
        if c_idx >= 0 and c_idx != self.mode_combo.currentIndex():
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(c_idx)
            self.mode_combo.blockSignals(False)
        m_idx = self.local_model_combo.findData(self.config.local_model)
        if m_idx >= 0 and m_idx != self.local_model_combo.currentIndex():
            self.local_model_combo.blockSignals(True)
            self.local_model_combo.setCurrentIndex(m_idx)
            self.local_model_combo.blockSignals(False)
        self._refresh_detail_card()

    def _refresh_detail_card(self) -> None:
        """Rebuild the contextual detail card under the hero switch.
        Local mode → model picker. Cloud mode → provider picker. Both as
        click-to-open chips so the user has clear visual targets instead
        of a dropdown that's mysteriously disabled half the time.

        v0.5.7: clear NESTED layouts too — the old loop only saw direct
        widgets via item.widget() and silently skipped sub-QVBoxLayouts.
        Result was "Local model" label still painted on top of "Cloud
        provider" after a Local→Cloud toggle (TJ-report: "Cloud provider
        ist doppelt überschrieben").
        """
        self._clear_layout_recursive(self._detail_layout)

        if self.config.mode == "local":
            self._build_local_detail()
        else:
            self._build_cloud_detail()

    @staticmethod
    def _clear_layout_recursive(layout) -> None:
        """Remove every widget AND every nested sub-layout from `layout`.

        Qt's QLayoutItem can be a widget OR a layout OR a spacer.  The
        previous version only handled widgets, so any sub-QVBoxLayout's
        QLabels would survive a rebuild and stack on top of the new ones.
        """
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
                continue
            sub = item.layout()
            if sub is not None:
                MainWindow._clear_layout_recursive(sub)
                sub.deleteLater()

    def _build_local_detail(self) -> None:
        # Title block
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        t = QLabel("Local model")
        t.setObjectName("h2")
        title_box.addWidget(t)
        sub = QLabel(self._hw_summary)
        sub.setObjectName("dim")
        title_box.addWidget(sub)
        self._detail_layout.addLayout(title_box, 1)

        # Current selection chip (click → open menu)
        chip = QPushButton(self.config.local_model)
        chip.setObjectName("primary")
        chip.setMinimumWidth(160)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.clicked.connect(lambda: self._popup_local_model_menu(chip))
        self._detail_layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)

    def _build_cloud_detail(self) -> None:
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        t = QLabel("Cloud provider")
        t.setObjectName("h2")
        title_box.addWidget(t)
        sub = QLabel("Routed to your chosen API")
        sub.setObjectName("dim")
        title_box.addWidget(sub)
        self._detail_layout.addLayout(title_box, 1)

        # 2026-05-16: Quality vs Fast toggle. Quality = large-v3-turbo
        # (the current default, best accuracy). Fast = distil-large-v3
        # for instant-paste feel on hotkey release. Only renders for
        # the Subunit cloud provider — Groq/OpenAI/Custom use their own
        # model selection.
        if self.config.mode == "subunit":
            quality_box = self._build_quality_toggle()
            self._detail_layout.addLayout(quality_box, 0)

        cur = self.config.mode
        chip_label = mode_label(cur).split("·")[0].strip()
        chip = QPushButton(chip_label)
        chip.setObjectName("primary")
        chip.setMinimumWidth(180)
        chip.setCursor(Qt.CursorShape.PointingHandCursor)
        chip.clicked.connect(lambda: self._popup_cloud_menu(chip))
        self._detail_layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)

    def _build_quality_toggle(self) -> QVBoxLayout:
        """Four-pill segmented control: AUTO | INSTANT | FAST | QUALITY.

        v0.9.7: added Auto.
        v0.9.9: added Instant tier (base model, ~12x faster than turbo).
        Server-side Auto routing: <5s → Instant, 5-20s → Fast, ≥20s → Quality.
        Auto is the default — fits short, snappy dictations AND long
        accurate meetings without the user toggling.
        """
        from PyQt6.QtWidgets import QButtonGroup

        col = QVBoxLayout()
        col.setSpacing(4)
        lbl = QLabel("Mode")
        lbl.setObjectName("dim")
        lbl.setStyleSheet(f"color: {WHITE_DIM}; font-size: 11px; letter-spacing: 1px;")
        col.addWidget(lbl)

        row = QHBoxLayout()
        row.setSpacing(0)

        current = (getattr(self.config, "cloud_quality_mode", "auto") or "auto").lower()
        group = QButtonGroup(self)
        group.setExclusive(True)
        for value, label in (
            ("auto",    "AUTO"),
            ("instant", "INSTANT"),
            ("fast",    "FAST"),
            ("quality", "QUALITY"),
        ):
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setMinimumWidth(62)
            b.setObjectName("qualityPillActive" if value == current else "qualityPill")
            b.setProperty("modeValue", value)
            if value == current:
                b.setChecked(True)
            b.clicked.connect(lambda _=False, v=value: self._on_quality_mode_changed(v))
            group.addButton(b)
            row.addWidget(b)
        col.addLayout(row)
        return col

    def _on_quality_mode_changed(self, value: str) -> None:
        if value not in ("auto", "instant", "fast", "quality"):
            return
        if getattr(self.config, "cloud_quality_mode", "quality") == value:
            return
        self.config.cloud_quality_mode = value
        self.config.save()
        # Bust the transcriber cache so the next call carries the new
        # quality_mode through to SubunitTranscriber.
        try:
            from ..transcriber.base import clear_cache as _clear
            _clear()
        except Exception:
            pass
        self._refresh_detail_card()

    def _popup_local_model_menu(self, anchor: QPushButton) -> None:
        menu = QMenu(self)
        for m in LOCAL_MODELS:
            label = m
            if m == self._recommended_model:
                label += "  ⭐"
            act = menu.addAction(label)
            act.setCheckable(True)
            if m == self.config.local_model:
                act.setChecked(True)

            def _pick(_=None, mm=m):
                self.config.local_model = mm
                self.config.save()
                if self.config.mode == "local":
                    self._on_change_mode("local")  # invalidate transcriber cache
                self._refresh_detail_card()

            act.triggered.connect(_pick)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _popup_cloud_menu(self, anchor: QPushButton) -> None:
        menu = QMenu(self)
        for mode_id in CLOUD_MODES:
            label = mode_label(mode_id).split("·")[0].strip()
            if mode_id == "subunit":
                label += "   ⭐"
            act = menu.addAction(label)
            act.setCheckable(True)
            if mode_id == self.config.mode:
                act.setChecked(True)

            def _pick(_=None, mm=mode_id):
                self.config.last_cloud_mode = mm
                self._on_change_mode(mm)
                self._refresh_detail_card()

            act.triggered.connect(_pick)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def refresh(self) -> None:
        self.stat_count.findChild(QLabel, "value").setText(
            str(self.config.total_transcriptions)
        )
        self.stat_audio.findChild(QLabel, "value").setText(
            _format_seconds(self.config.total_audio_seconds)
        )
        self.stat_saved.findChild(QLabel, "value").setText(
            _format_seconds(self.config.total_audio_seconds * 2.5)
        )
        self.hotkey_lbl.setText(f"Hotkey: {self.config.hotkey}")

        # Refill recent list (top 10, newest first)
        self.history_list.clear()
        recent = list(reversed(self.config.history[-10:]))
        if not recent:
            empty = QListWidgetItem("No transcriptions yet — press your hotkey to begin")
            empty.setForeground(QColor(WHITE_DIM))
            self.history_list.addItem(empty)
            return
        for entry in recent:
            text = entry.get("text", "")
            ts = entry.get("ts", "")
            mode = entry.get("mode", "")
            line1 = text if len(text) <= 100 else text[:97] + "…"
            label = f"{line1}\n{ts} · {mode}"
            it = QListWidgetItem(label)
            self.history_list.addItem(it)

    # ── private ────────────────────────────────────────────────────────────

    def _on_mode_changed(self, _idx: int) -> None:
        m = self.mode_combo.currentData()
        if m:
            self.config.last_cloud_mode = m
            self._on_change_mode(m)

    def _on_local_toggled(self, checked: bool) -> None:
        if checked:
            self._on_change_mode("local")
        else:
            target = self.mode_combo.currentData() or self.config.last_cloud_mode or "subunit"
            self._on_change_mode(target)
        self._refresh_detail_card()

    def _on_local_model_changed(self, _idx: int) -> None:
        m = self.local_model_combo.currentData()
        if not m or m == self.config.local_model:
            return
        self.config.local_model = m
        self.config.save()
        # Re-apply local mode so the transcriber cache is invalidated and the
        # new model loads on the next hotkey press.
        if self.config.mode == "local":
            self._on_change_mode("local")

    def _make_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        return card

    def _make_stat_card(self, title: str, value: str) -> QFrame:
        card = self._make_card()
        l = QVBoxLayout(card)
        # 2026-05-16: roomier padding so the values don't kiss the edges
        # in the bigger main window. Cards are equal-weight in the stats
        # row — give them visual breathing space.
        l.setContentsMargins(22, 20, 22, 20)
        l.setSpacing(6)
        t = QLabel(title)
        t.setObjectName("h2")
        v = QLabel(value)
        v.setObjectName("big")
        v.setProperty("class", "value")
        v.setObjectName("value")  # findChild lookup
        l.addWidget(t)
        l.addWidget(v)
        return card

    def closeEvent(self, e) -> None:
        # Hide instead of quit — feels more like an installed app.
        e.ignore()
        self.hide()
