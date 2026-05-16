"""Floating Orb overlay — v0.3.11 redesign.

A small, persistent dot that lives at the bottom-center of the screen.
Subtle pulse while idle, color-coded reaction during recording /
transcribing. Hover reveals 3 satellite dots fanning N / W / E for
language / mode / cleanup-style. Each satellite either opens a popup
picker (lang) or a tiny 2-bubble pill (mode / style) for visual choice.

Replaces v0.3.5–v0.3.8's 9-sphere bouquet — TJ called the cluster
"viel zu groß" + "dreht sich". This one is intentionally Voicely-sized:
tiny dot with the smallest possible footprint, pop-out details only on
demand.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

from PyQt6.QtCore import (
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QWidget

from ..config import Config
from ..transcriber import CLOUD_MODES, mode_label

# ── Palette ────────────────────────────────────────────────────────────────
CYAN = QColor(64, 214, 255)
DEEP_CYAN = QColor(20, 96, 130)
NIGHT = QColor(2, 8, 23)
GLASS_DARK = QColor(8, 16, 30, 235)
GLASS_RIM = QColor(255, 255, 255, 90)
WHITE = QColor(255, 255, 255)
WHITE_DIM = QColor(255, 255, 255, 200)
RED = QColor(255, 88, 92)
GREEN = QColor(80, 220, 130)


COLOR_THEMES = {
    "cyan": (CYAN, DEEP_CYAN),
    "violet": (QColor(170, 110, 255), QColor(70, 30, 130)),
    "mint": (QColor(110, 230, 190), QColor(20, 110, 90)),
}


class OrbOverlay(QWidget):
    """Tiny dot, big behaviour. ~28px core, ~98px total window
    (room for halo + satellites). Every pixel here is intentional —
    bigger and it dominates the desktop, smaller and the satellites
    get unclickable.
    """

    DOT_RADIUS = 14            # the visible orb itself (~28px diameter)
    # v0.5.11 (TJ-feedback): satellites were "very tiny", make them the
    # same size as the main dot since they only appear on hover/click
    # anyway — no clutter risk, and clicking 28px targets is much
    # easier than 18px ones (esp. on touch + Win-on-ARM tablets).
    SAT_RADIUS = 14            # was 9; matches DOT_RADIUS now
    # Bigger satellites need more breathing room from the orb center,
    # otherwise they kiss the orb edge.  Push them out enough to leave
    # a small visible gap.
    SAT_DISTANCE = 34          # was 26
    # And the window needs more padding so the satellites + halo fit.
    PADDING = 52               # was 38

    def __init__(self, config: Config, on_change_mode: Callable[[str], None]) -> None:
        super().__init__()
        self.config = config
        self._on_change_mode = on_change_mode
        self._level_provider: Optional[Callable[[], float]] = None
        self._state = "idle"
        self._pulse_phase = 0.0
        self._level = 0.0
        self._level_smooth = 0.0
        self._hovered = False
        self._theme = config.orb_color_theme or "cyan"
        self._drag_origin: Optional[QPoint] = None
        # Currently-open sub-popup (mode/style/lang) so we can close it
        # if the user clicks elsewhere.
        self._popup: Optional[QWidget] = None
        self._satellite_opacity = 0.0  # animated 0..1 fade for satellites

        self.setMouseTracking(True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.X11BypassWindowManagerHint
            # v0.5.11 (TJ-feedback): "bei Windows ist um diesen Dot
            # herum so ein Kasten ... der muss weg".  Win11's DWM
            # paints a subtle 1px outline / shadow around translucent
            # tool windows by default.  NoDropShadowWindowHint asks the
            # window manager to skip the drop shadow, which on Windows
            # also removes the outline that made the dot look boxed-in.
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # v0.5.11: belt-and-braces — also disable the system background
        # painting so no compositor fill leaks through the alpha layer
        # on Win11 themes that ignore WA_TranslucentBackground partially.
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        side = (self.DOT_RADIUS + self.PADDING) * 2
        self.resize(side, side)

        # v0.3.24: click-through outside the visible dot. The window itself
        # is much larger than the dot (PADDING reserves room for halo + the
        # 3 satellites), but clicks on those empty pixels should pass
        # through to the app underneath. We mask the hit-testable region
        # to a circle around the dot while idle. When the user actually
        # hovers the dot, _on_tick expands the mask to include the
        # satellites so they remain clickable.
        self._update_input_mask(hovered=False)

        self._tick = QTimer(self)
        self._tick.setInterval(33)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self._reposition()

    def _update_input_mask(self, hovered: bool) -> None:
        """Set a circular hit-test mask. Idle = just the dot (clicks pass
        through everywhere else). Hovered/active = full window so the
        satellites + halo react to clicks."""
        from PyQt6.QtGui import QRegion

        cx = cy = self.DOT_RADIUS + self.PADDING
        if hovered:
            # Full bounds — satellites + halo are interactive.
            r = self.DOT_RADIUS + self.PADDING
        else:
            # Just the dot + a couple of pixels for forgiving hover.
            r = self.DOT_RADIUS + 4
        self.setMask(QRegion(cx - r, cy - r, r * 2, r * 2, QRegion.RegionType.Ellipse))

    # ── Public API ─────────────────────────────────────────────────────────

    def set_level_provider(self, provider: Optional[Callable[[], float]]) -> None:
        self._level_provider = provider

    def show_state(self, state: str, _text: str = "") -> None:
        self._state = state
        if not self.isVisible():
            self.show()
        if state in ("done", "error"):
            QTimer.singleShot(900, lambda: self._maybe_reset_state(state))

    def _maybe_reset_state(self, from_state: str) -> None:
        if self._state == from_state:
            self._state = "idle"
            self.update()

    # ── Geometry / placement ───────────────────────────────────────────────

    def _reposition(self) -> None:
        """Place the orb according to config.orb_position. Default is
        bottom-center (TJ explicitly asked for mittig, not corner)."""
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        margin = 20
        pos = (self.config.orb_position or "bottom-center").strip()

        if pos.startswith("custom-"):
            try:
                _, sx, sy = pos.split("-", 2)
                x = rect.x() + max(0, min(rect.width() - self.width(), int(sx)))
                y = rect.y() + max(0, min(rect.height() - self.height(), int(sy)))
                self.move(x, y)
                return
            except (ValueError, IndexError):
                pass

        if pos == "top-left":
            x, y = rect.x() + margin, rect.y() + margin
        elif pos == "top-right":
            x, y = rect.x() + rect.width() - self.width() - margin, rect.y() + margin
        elif pos == "top-center":
            x = rect.x() + (rect.width() - self.width()) // 2
            y = rect.y() + margin
        elif pos == "bottom-left":
            x, y = rect.x() + margin, rect.y() + rect.height() - self.height() - margin
        elif pos == "bottom-right":
            x = rect.x() + rect.width() - self.width() - margin
            y = rect.y() + rect.height() - self.height() - margin
        else:  # "bottom-center" (default)
            x = rect.x() + (rect.width() - self.width()) // 2
            y = rect.y() + rect.height() - self.height() - margin
        self.move(x, y)

    # ── Tick / state animation ─────────────────────────────────────────────

    def _on_tick(self) -> None:
        self._pulse_phase += 0.06
        if self._level_provider is not None:
            try:
                self._level = float(self._level_provider())
            except Exception:
                self._level = 0.0
        self._level_smooth += (self._level - self._level_smooth) * 0.3

        # Fade satellites in/out smoothly when hover state changes
        target = 1.0 if self._hovered else 0.0
        self._satellite_opacity += (target - self._satellite_opacity) * 0.25

        self.update()

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = cy = self.DOT_RADIUS + self.PADDING
        accent, accent_deep = COLOR_THEMES.get(self._theme, COLOR_THEMES["cyan"])
        if self._state == "recording":
            accent, accent_deep = RED, QColor(120, 30, 40)
        elif self._state == "done":
            accent, accent_deep = GREEN, QColor(30, 90, 50)

        is_active = self._state != "idle"
        # 0 = fully dim idle, 1 = fully lit (recording/hover)
        lit = 1.0 if is_active else self._satellite_opacity

        # v0.7.1 / v0.9.6: dispatch to the configured renderer.
        style = getattr(self.config, "orb_overlay_style", "sphere") or "sphere"
        if style == "sonar":
            self._paint_sonar(p, cx, cy, accent, accent_deep, lit, is_active)
        elif style == "bars":
            self._paint_bars(p, cx, cy, accent, accent_deep, lit, is_active)
        elif style == "wave":
            self._paint_wave(p, cx, cy, accent, accent_deep, lit, is_active)
        elif style == "classic":
            self._paint_classic(p, cx, cy, accent, accent_deep, lit, is_active)
        elif style == "ping":
            self._paint_ping(p, cx, cy, accent, accent_deep, lit, is_active)
        elif style == "pill":
            self._paint_pill(p, cx, cy, accent, accent_deep, lit, is_active)
        elif style == "constellation":
            self._paint_constellation(p, cx, cy, accent, accent_deep, lit, is_active)
        else:
            self._paint_sphere(p, cx, cy, accent, accent_deep, lit, is_active)

        # Satellite dots (faded by hover-opacity so they dissolve in/out).
        # Shared across all renderers so the picker UX stays consistent.
        if self._satellite_opacity > 0.02:
            self._draw_satellites(p, cx, cy, accent)

    # ------------------------------------------------------------------
    # Renderer: sphere (v0.4 default — Voicely-style glass dot)
    # ------------------------------------------------------------------
    def _paint_sphere(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        # Outer halo — only when lit. Idle = no halo at all.
        if lit > 0.01:
            if self.config.orb_idle_pulse or is_active:
                breath = 0.5 + 0.5 * math.sin(self._pulse_phase * 0.7)
            else:
                breath = 0.5
            halo_strength = (
                0.55 * breath + self._level_smooth * 1.4
                if is_active
                else 0.35 * breath
            ) * lit
            for i in range(self.PADDING - 14, 0, -3):
                alpha = int(34 * halo_strength * (1 - i / (self.PADDING - 14)))
                if alpha <= 0:
                    continue
                color = QColor(accent)
                color.setAlpha(alpha)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color)
                p.drawEllipse(
                    int(cx - self.DOT_RADIUS - i),
                    int(cy - self.DOT_RADIUS - i),
                    int((self.DOT_RADIUS + i) * 2),
                    int((self.DOT_RADIUS + i) * 2),
                )

        # Main dot — colored when lit (hover/active), dim grey when idle.
        # Blends along `lit` so the transition is smooth.
        if lit > 0.01:
            grad = QRadialGradient(cx - 3, cy - 4, self.DOT_RADIUS * 1.4)
            grad.setColorAt(0.0, _blend(_DIM_DOT, _bright(accent, 30), lit))
            grad.setColorAt(0.6, _blend(_DIM_DOT, accent, lit))
            grad.setColorAt(1.0, _blend(_DIM_DOT_DEEP, accent_deep, lit))
            p.setBrush(QBrush(grad))
        else:
            # Idle, not hovered — single muted grey, no gradient flair
            p.setBrush(_DIM_DOT)
        p.setPen(QPen(GLASS_RIM, 1.0))
        p.drawEllipse(
            cx - self.DOT_RADIUS, cy - self.DOT_RADIUS,
            self.DOT_RADIUS * 2, self.DOT_RADIUS * 2,
        )

        # Inner pulse — visible only when lit, signals "alive" without
        # being distracting in idle.
        if lit > 0.01:
            inner_r = max(
                2,
                int(
                    self.DOT_RADIUS * 0.35
                    + 2.0 * math.sin(self._pulse_phase * 1.4)
                    + self._level_smooth * (self.DOT_RADIUS * 0.5)
                ),
            )
            inner_alpha = int((180 if is_active else 110) * lit)
            inner_color = QColor(255, 255, 255, inner_alpha)
            p.setBrush(inner_color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)

        # Specular highlight — also fades with lit
        if lit > 0.01:
            p.setBrush(QColor(255, 255, 255, int(90 * lit)))
            p.drawEllipse(
                cx - int(self.DOT_RADIUS * 0.55),
                cy - int(self.DOT_RADIUS * 0.65),
                max(2, int(self.DOT_RADIUS * 0.32)),
                max(2, int(self.DOT_RADIUS * 0.32)),
            )

    # ------------------------------------------------------------------
    # Renderer: sonar (animated Sonar logo — pulsing rings + audio bars)
    # ------------------------------------------------------------------
    def _paint_sonar(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        # Layout is keyed off DOT_RADIUS so it scales with the rest of the orb.
        # Outer ring radius == ~2.2x DOT_RADIUS, inner ~1.6x, bars span ~1.1x.
        outer_r = int(self.DOT_RADIUS * 2.2)
        inner_r = int(self.DOT_RADIUS * 1.6)
        bar_span = int(self.DOT_RADIUS * 1.1)
        bar_w = max(2, int(self.DOT_RADIUS * 0.18))
        bar_gap = max(1, int(self.DOT_RADIUS * 0.12))

        # Animated phases
        breath = 0.5 + 0.5 * math.sin(self._pulse_phase * 0.9)
        level = self._level_smooth

        # 1) Outer ring — faint always, gets brighter when lit.
        outer_alpha = int((40 + 70 * lit) * (0.6 + 0.4 * breath))
        outer_col = QColor(accent)
        outer_col.setAlpha(outer_alpha)
        p.setPen(QPen(outer_col, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - outer_r, cy - outer_r, outer_r * 2, outer_r * 2)

        # 2) Inner ring — slightly brighter.
        inner_alpha = int((90 + 90 * lit) * (0.6 + 0.4 * breath))
        inner_col = QColor(accent)
        inner_col.setAlpha(inner_alpha)
        p.setPen(QPen(inner_col, 1.8))
        p.drawEllipse(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)

        # 3) Sonar ping — when active, expand a ring outward beyond the outer
        # ring, fading as it travels. Period synced to pulse_phase.
        if is_active or lit > 0.5:
            ping_phase = (self._pulse_phase * 0.55) % 1.0
            ping_r = inner_r + (outer_r - inner_r + self.PADDING - 6) * ping_phase
            ping_alpha = int(160 * (1.0 - ping_phase) * lit)
            if ping_alpha > 0:
                ping_col = QColor(accent)
                ping_col.setAlpha(ping_alpha)
                p.setPen(QPen(ping_col, 1.4))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(int(cx - ping_r), int(cy - ping_r), int(ping_r * 2), int(ping_r * 2))

        # 4) Five vertical bars — heights from the SVG: 8, 16, 32, 24, 12
        # (relative units). They modulate with audio level (recording) or a
        # gentle breathing motion (idle).
        bar_heights_base = [8.0, 16.0, 32.0, 24.0, 12.0]
        n = len(bar_heights_base)
        total_w = n * bar_w + (n - 1) * bar_gap
        start_x = cx - total_w // 2

        # Scale: bars in the SVG span 32 units max; we map that to bar_span px.
        unit_px = bar_span / 32.0

        # Modulation: idle = subtle breathing, active = audio-reactive.
        if is_active:
            scale = 0.45 + 1.55 * level + 0.15 * breath
        else:
            scale = 0.42 + 0.18 * breath
        scale *= 0.7 + 0.3 * lit

        # Per-bar phase offset so they don't all wiggle in unison when idle.
        for i, base in enumerate(bar_heights_base):
            phase_off = math.sin(self._pulse_phase * 1.4 + i * 0.9) * 0.10
            h = max(2, int(base * unit_px * (scale + phase_off)))
            x = start_x + i * (bar_w + bar_gap)
            y = cy - h // 2
            col = QColor(accent if is_active or lit > 0.5 else _DIM_DOT)
            col_alpha_base = 200 if is_active else 90 + int(120 * lit)
            col.setAlpha(min(255, col_alpha_base))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(x, y, bar_w, h, bar_w / 2, bar_w / 2)

    # ------------------------------------------------------------------
    # Renderer: bars (graphic-EQ-style vertical bars)
    # ------------------------------------------------------------------
    def _paint_bars(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        n = 9
        bar_w = max(2, int(self.DOT_RADIUS * 0.30))
        bar_gap = max(1, int(self.DOT_RADIUS * 0.18))
        total_w = n * bar_w + (n - 1) * bar_gap
        start_x = cx - total_w // 2
        max_h = int(self.DOT_RADIUS * 2.4)
        level = self._level_smooth

        for i in range(n):
            # Center bars taller than edges, all audio-reactive.
            falloff = 1.0 - abs(i - n // 2) / (n // 2 + 1)
            phase = math.sin(self._pulse_phase * 1.6 + i * 0.6)
            if is_active:
                amp = 0.30 + level * 1.5 + 0.10 * phase
            else:
                amp = 0.18 + 0.10 * (0.5 + 0.5 * phase)
            h = max(2, int(max_h * falloff * amp * (0.6 + 0.4 * lit)))
            x = start_x + i * (bar_w + bar_gap)
            y = cy - h // 2
            col = QColor(accent if is_active or lit > 0.5 else _DIM_DOT)
            col.setAlpha(min(255, 90 + int(160 * lit)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(x, y, bar_w, h, bar_w / 2, bar_w / 2)

    # ------------------------------------------------------------------
    # Renderer: wave (horizontal sine waveform)
    # ------------------------------------------------------------------
    def _paint_wave(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        from PyQt6.QtGui import QPainterPath
        span = int(self.DOT_RADIUS * 2.6)
        amp_base = self.DOT_RADIUS * 0.8
        level = self._level_smooth
        amp = amp_base * ((0.35 + level * 1.6) if is_active else (0.18 + 0.10 * math.sin(self._pulse_phase * 1.2)))
        amp *= 0.6 + 0.4 * lit

        path = QPainterPath()
        steps = 64
        for i in range(steps + 1):
            t = i / steps
            x = cx - span // 2 + int(t * span)
            phase = self._pulse_phase * 1.8 + t * 6.28 * 2
            y = cy + int(amp * math.sin(phase))
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        col = QColor(accent if is_active or lit > 0.5 else _DIM_DOT)
        col.setAlpha(min(255, 110 + int(140 * lit)))
        p.setPen(QPen(col, 2.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)

    # ------------------------------------------------------------------
    # Renderer: classic (minimal cyan dot — Bubble-era throwback)
    # ------------------------------------------------------------------
    def _paint_classic(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        r = max(4, int(self.DOT_RADIUS * (0.95 + 0.25 * self._level_smooth)))
        col = QColor(accent if is_active or lit > 0.5 else _DIM_DOT)
        col.setAlpha(min(255, 110 + int(140 * lit)))
        p.setPen(QPen(GLASS_RIM, 1.0))
        p.setBrush(col)
        p.drawEllipse(cx - r, cy - r, r * 2, r * 2)

    # ------------------------------------------------------------------
    # Renderer: ping (v0.9.6 — mic-reactive expanding sonar rings)
    # ------------------------------------------------------------------
    def _paint_ping(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        """Center dot with 3 concentric rings that swell outward in sync
        with mic level. Idle = single faint ring. Recording = bright
        rings reaching the widget edge, brightness modulated by audio
        level so the user can SEE their own voice."""
        max_r = int(self.DOT_RADIUS + self.PADDING - 6)
        level = self._level_smooth
        breath = 0.5 + 0.5 * math.sin(self._pulse_phase * 1.2)

        # Center dot
        core_r = max(4, int(self.DOT_RADIUS * 0.55 + self._level_smooth * 4))
        col = QColor(accent if is_active or lit > 0.4 else _DIM_DOT)
        col.setAlpha(min(255, 130 + int(125 * lit)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(cx - core_r, cy - core_r, core_r * 2, core_r * 2)

        # 3 rings — each at a different phase of the pulse so they look
        # like they're moving outward continuously when animated.
        ring_count = 3
        for i in range(ring_count):
            phase = (self._pulse_phase * 0.9 + i * (2 * math.pi / ring_count)) % (2 * math.pi)
            t = (phase / (2 * math.pi))  # 0..1 — ring position from center → edge
            # Audio level boosts how far rings reach AND their intensity
            reach = self.DOT_RADIUS + 8 + int((max_r - self.DOT_RADIUS - 8) * t)
            base_alpha = (1.0 - t) ** 1.6  # fade as ring expands
            alpha = int((28 + 150 * (level if is_active else 0.20 * breath)) * base_alpha * lit)
            if alpha <= 2:
                continue
            ring_col = QColor(accent)
            ring_col.setAlpha(min(255, alpha))
            p.setPen(QPen(ring_col, 1.8))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(cx - reach, cy - reach, reach * 2, reach * 2)

    # ------------------------------------------------------------------
    # Renderer: pill (v0.9.6 — compact status badge with dot)
    # ------------------------------------------------------------------
    def _paint_pill(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        """Horizontal pill with a small status dot and (during active
        states) a brief text label. Compact, reads like a toast."""
        from PyQt6.QtGui import QFont

        # Label
        label_map = {
            "idle": "",
            "recording": "REC",
            "transcribing": "...",
            "done": "OK",
            "error": "ERR",
        }
        label = label_map.get(self._state, "")
        if self._state == "idle" and lit < 0.1:
            label = ""

        # Sizing
        pill_h = max(20, int(self.DOT_RADIUS * 1.6))
        text_pad = 10 if label else 0
        # Measure text width
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110)
        p.setFont(font)
        text_w = p.fontMetrics().horizontalAdvance(label) if label else 0
        dot_d = max(6, int(pill_h * 0.42))
        # left padding + dot + gap + text + right padding
        pill_w = 12 + dot_d + (8 + text_w if label else 0) + 12

        pill_x = cx - pill_w // 2
        pill_y = cy - pill_h // 2

        # Background
        bg = QColor(10, 22, 40, 235)
        p.setBrush(bg)
        col = QColor(accent if is_active or lit > 0.4 else QColor(80, 90, 110))
        col.setAlpha(min(255, 90 + int(165 * lit)))
        p.setPen(QPen(col, 1.4))
        p.drawRoundedRect(pill_x, pill_y, pill_w, pill_h, pill_h // 2, pill_h // 2)

        # Status dot
        dot_x = pill_x + 12
        dot_y = cy - dot_d // 2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(dot_x, dot_y, dot_d, dot_d)

        # Text
        if label:
            text_col = QColor(col)
            text_col.setAlpha(255)
            p.setPen(text_col)
            text_x = dot_x + dot_d + 8
            p.drawText(QRect(text_x, pill_y, text_w + 4, pill_h),
                       int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                       label)

    # ------------------------------------------------------------------
    # Renderer: constellation (v0.9.6 — rotating dots around a center)
    # ------------------------------------------------------------------
    def _paint_constellation(
        self,
        p: QPainter,
        cx: int,
        cy: int,
        accent: QColor,
        accent_deep: QColor,
        lit: float,
        is_active: bool,
    ) -> None:
        """Central node with 6 satellite dots orbiting at 2 different
        radii. The whole formation slowly rotates; mic level brightens
        the connecting lines."""
        rot = self._pulse_phase * 0.5  # rotation in radians, slow
        # Two orbit radii — front/back layered
        radii = [int(self.DOT_RADIUS * 1.5), int(self.DOT_RADIUS * 2.4)]
        counts = [3, 4]
        level = self._level_smooth

        # Connecting lines first (so dots draw on top)
        if lit > 0.05:
            line_alpha = int((30 + 130 * (level if is_active else 0.30)) * lit)
            line_col = QColor(accent)
            line_col.setAlpha(min(255, line_alpha))
            p.setPen(QPen(line_col, 1.2))
            for ri, (radius, count) in enumerate(zip(radii, counts)):
                for j in range(count):
                    a = rot + ri * 0.7 + j * (2 * math.pi / count)
                    dx = cx + int(math.cos(a) * radius)
                    dy = cy + int(math.sin(a) * radius)
                    p.drawLine(cx, cy, dx, dy)

        # Center node
        core_r = max(5, int(self.DOT_RADIUS * 0.55 + self._level_smooth * 3))
        col = QColor(accent if is_active or lit > 0.4 else _DIM_DOT)
        col.setAlpha(min(255, 140 + int(115 * lit)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(cx - core_r, cy - core_r, core_r * 2, core_r * 2)

        # Satellite dots
        for ri, (radius, count) in enumerate(zip(radii, counts)):
            for j in range(count):
                a = rot + ri * 0.7 + j * (2 * math.pi / count)
                dx = cx + int(math.cos(a) * radius)
                dy = cy + int(math.sin(a) * radius)
                # Outer ring dots smaller + dimmer to suggest depth
                size = max(3, int(self.DOT_RADIUS * (0.32 if ri == 0 else 0.22)))
                dot_col = QColor(accent if is_active or lit > 0.4 else QColor(80, 90, 110))
                dot_col.setAlpha(min(255, (170 if ri == 0 else 130) + int(85 * lit)))
                p.setBrush(dot_col)
                p.drawEllipse(dx - size, dy - size, size * 2, size * 2)

    def _draw_satellites(
        self, p: QPainter, cx: int, cy: int, accent: QColor
    ) -> None:
        op = self._satellite_opacity
        positions = self._satellite_positions(cx, cy)
        for name, (sx, sy) in positions.items():
            # Soft halo
            for i in range(4, 0, -1):
                alpha = int(op * 28 * (1 - i / 4))
                color = QColor(accent)
                color.setAlpha(alpha)
                p.setBrush(color)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(
                    sx - self.SAT_RADIUS - i,
                    sy - self.SAT_RADIUS - i,
                    (self.SAT_RADIUS + i) * 2,
                    (self.SAT_RADIUS + i) * 2,
                )
            # Body
            grad = QRadialGradient(sx - 2, sy - 3, self.SAT_RADIUS * 1.3)
            bright = QColor(accent.red(), accent.green(), accent.blue(), int(255 * op))
            dim = QColor(GLASS_DARK)
            dim.setAlpha(int(235 * op))
            grad.setColorAt(0.0, bright)
            grad.setColorAt(1.0, dim)
            p.setBrush(QBrush(grad))
            rim = QColor(GLASS_RIM)
            rim.setAlpha(int(GLASS_RIM.alpha() * op))
            p.setPen(QPen(rim, 0.8))
            p.drawEllipse(
                sx - self.SAT_RADIUS, sy - self.SAT_RADIUS,
                self.SAT_RADIUS * 2, self.SAT_RADIUS * 2,
            )
            # Tiny indicator inside
            self._draw_satellite_indicator(p, name, sx, sy, op)

    def _draw_satellite_indicator(
        self, p: QPainter, name: str, sx: int, sy: int, op: float
    ) -> None:
        """A 1-glyph hint in each satellite. Kept minimal — the popup
        on click does the heavy lifting."""
        white = QColor(255, 255, 255, int(220 * op))
        p.setPen(QPen(white, 1.2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "top":
            # Lock = local. Open arc = cloud. Differentiate by current mode.
            is_local = self.config.mode == "local"
            if is_local:
                # Closed lock body
                p.drawRect(sx - 3, sy - 1, 6, 4)
                # shackle
                path = QPainterPath()
                path.moveTo(sx - 2, sy - 1)
                path.lineTo(sx - 2, sy - 3)
                path.quadTo(sx, sy - 5, sx + 2, sy - 3)
                path.lineTo(sx + 2, sy - 1)
                p.drawPath(path)
            else:
                # Cloud — three little bumps
                p.drawEllipse(sx - 4, sy - 1, 3, 3)
                p.drawEllipse(sx - 1, sy - 2, 4, 4)
                p.drawEllipse(sx + 2, sy - 1, 3, 3)
        elif name == "left":
            # "Aa" globe-ish — render the language code in tiny font
            f = p.font()
            f.setPointSize(7)
            f.setBold(True)
            p.setFont(f)
            p.setPen(white)
            r = QRect(sx - self.SAT_RADIUS, sy - self.SAT_RADIUS,
                       self.SAT_RADIUS * 2, self.SAT_RADIUS * 2)
            # v0.6.0: render "auto" as ⇋ (auto-detect glyph) instead of
            # the literal "AU" letters, so the satellite stays legible
            # at small sizes.
            lang_code = (self.config.language or "DE").lower()
            label = "⇋" if lang_code in ("auto", "") else lang_code.upper()[:2]
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), label)
        elif name == "right":
            # Sparkle — diagonal cross + tiny dot
            p.drawLine(sx - 3, sy, sx + 3, sy)
            p.drawLine(sx, sy - 3, sx, sy + 3)

    def _satellite_positions(self, cx: int, cy: int) -> dict[str, tuple[int, int]]:
        return {
            "top": (cx, cy - self.SAT_DISTANCE),
            "left": (cx - self.SAT_DISTANCE, cy),
            "right": (cx + self.SAT_DISTANCE, cy),
        }

    # ── Mouse ──────────────────────────────────────────────────────────────

    def enterEvent(self, _e) -> None:
        self._hovered = True
        # v0.3.24: expand hit-test mask so satellites become clickable
        # while we're hovered. The mask shrinks back on leave.
        self._update_input_mask(hovered=True)
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hovered = False
        self._update_input_mask(hovered=False)
        self.update()

    def mousePressEvent(self, e) -> None:
        # v0.5.11: left-click-drag on the dot itself = move the orb.
        # Discoverable (just grab and move; right-click drag was a
        # hidden gesture nobody knew about — TJ-report: "man muss den
        # Dot in Custom Positions geben können").  We only START a
        # potential drag here; the actual `move()` only kicks in once
        # the cursor has travelled DRAG_THRESHOLD pixels, so a normal
        # click on the dot (to interact with satellites later) doesn't
        # accidentally re-position it.
        cx = cy = self.DOT_RADIUS + self.PADDING
        pos = e.position()
        cx_mouse, cy_mouse = pos.x(), pos.y()
        on_dot = math.hypot(cx_mouse - cx, cy_mouse - cy) <= self.DOT_RADIUS + 2

        if e.button() == Qt.MouseButton.RightButton:
            # Right-click drag — kept as a power-user shortcut + back-compat.
            self._drag_origin = e.globalPosition().toPoint() - self.pos()
            self._drag_started = True  # right-click drag is always immediate
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        if e.button() == Qt.MouseButton.LeftButton and on_dot:
            # Set up a pending drag; only commits if user moves > threshold.
            self._drag_origin = e.globalPosition().toPoint() - self.pos()
            self._drag_press_pos = e.globalPosition().toPoint()
            self._drag_started = False
            return

        # Only react to left-clicks on satellites when they're visible.
        if self._satellite_opacity < 0.4:
            return
        for name, (sx, sy) in self._satellite_positions(cx, cy).items():
            if math.hypot(cx_mouse - sx, cy_mouse - sy) <= self.SAT_RADIUS + 2:
                self._handle_satellite(name, sx, sy)
                return

    DRAG_THRESHOLD = 6  # pixels — distinguish click from drag

    def mouseMoveEvent(self, e) -> None:
        if self._drag_origin is not None:
            # If we're still in "pending" mode (left-click), only start
            # actually moving once cursor has travelled past the threshold.
            if not getattr(self, "_drag_started", True):
                if (e.globalPosition().toPoint() - self._drag_press_pos).manhattanLength() < self.DRAG_THRESHOLD:
                    return
                self._drag_started = True
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.move(e.globalPosition().toPoint() - self._drag_origin)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_origin is not None and e.button() in (
            Qt.MouseButton.RightButton, Qt.MouseButton.LeftButton
        ):
            was_dragging = getattr(self, "_drag_started", True)
            self._drag_origin = None
            self._drag_started = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if was_dragging:
                screen = QApplication.screenAt(self.pos()) or QApplication.primaryScreen()
                geom = screen.availableGeometry()
                sx = self.x() - geom.x()
                sy = self.y() - geom.y()
                self.config.orb_position = f"custom-{sx}-{sy}"
                self.config.save()
            return
        super().mouseReleaseEvent(e)

    # ── Sub-popups (mode / style / lang) ───────────────────────────────────

    def _handle_satellite(self, name: str, sx_local: int, sy_local: int) -> None:
        # Translate satellite anchor from widget-local to global so
        # popups land next to the right satellite.
        anchor_global = self.mapToGlobal(QPoint(int(sx_local), int(sy_local)))

        if name == "top":
            self._open_choice_popup(
                anchor_global,
                title="Mode",
                options=[
                    ("local", "Local"),
                    (self.config.last_cloud_mode or "subunit", "Cloud"),
                ],
                current=self.config.mode if self.config.mode == "local" else "cloud",
                on_pick=self._pick_mode,
            )
        elif name == "right":
            current = "off" if not self.config.cleanup_enabled else self.config.cleanup_style
            self._open_choice_popup(
                anchor_global,
                title="Cleanup",
                options=[
                    ("off", "Off"),
                    ("prompt", "Prompt"),
                    ("email", "Email"),
                    ("slack", "Slack"),
                    ("formal", "Formal"),
                ],
                current=current,
                on_pick=self._pick_style,
            )
        elif name == "left":
            self._open_lang_popup(anchor_global)

    def _pick_mode(self, choice: str) -> None:
        # The popup passes "local" or the cloud mode key directly.
        if choice == "local":
            self._on_change_mode("local")
        else:
            target = self.config.last_cloud_mode or "subunit"
            if choice in CLOUD_MODES:
                target = choice
            self._on_change_mode(target)
        self.update()

    def _pick_style(self, choice: str) -> None:
        if choice == "off":
            self.config.cleanup_enabled = False
        elif choice in ("prompt", "email", "slack", "formal"):
            self.config.cleanup_enabled = True
            self.config.cleanup_style = choice
        else:
            return
        self.config.save()
        self.update()

    def _open_choice_popup(
        self,
        anchor_global: QPoint,
        title: str,
        options: list[tuple[str, str]],
        current: str,
        on_pick: Callable[[str], None],
    ) -> None:
        """Tiny pill-shaped popup with N round bubbles for visual choice.
        Kept compact so it feels like a natural extension of the orb,
        not a full-window dialog."""
        self._close_popup()
        popup = ChoiceBubblePopup(title, options, current, on_pick)
        # Place above the satellite if there's room, else below.
        screen = QApplication.screenAt(anchor_global) or QApplication.primaryScreen()
        geom = screen.availableGeometry()
        px = anchor_global.x() - popup.width() // 2
        py = anchor_global.y() - popup.height() - 12
        if py < geom.y() + 8:
            py = anchor_global.y() + 18
        px = max(geom.x() + 8, min(px, geom.x() + geom.width() - popup.width() - 8))
        popup.move(px, py)
        popup.show()
        self._popup = popup

    def _open_lang_popup(self, anchor_global: QPoint) -> None:
        self._close_popup()
        from .lang_picker import LangPickerPopup

        def on_pick(code: str) -> None:
            self.config.language = code
            self.config.save()
            self.update()

        popup = LangPickerPopup(self.config.language, on_pick)
        screen = QApplication.screenAt(anchor_global) or QApplication.primaryScreen()
        geom = screen.availableGeometry()
        # Anchor: above the orb, centered. Flips below if it would clip.
        px = anchor_global.x() - popup.width() // 2
        py = anchor_global.y() - popup.height() - 12
        if py < geom.y() + 8:
            py = anchor_global.y() + 18
        px = max(geom.x() + 8, min(px, geom.x() + geom.width() - popup.width() - 8))
        popup.move(px, py)
        popup.show()
        self._popup = popup

    def _close_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup = None


# ── Choice bubble popup ──────────────────────────────────────────────────────


class ChoiceBubblePopup(QWidget):
    """Tiny horizontal pill with N round bubbles. Click one → on_pick + close.
    Used for Mode (Local/Cloud) and Style (Tidy/Formal) so the user picks
    visually instead of cycling through options."""

    BUBBLE_R = 26
    BUBBLE_GAP = 14
    PADDING = 14

    def __init__(
        self,
        title: str,
        options: list[tuple[str, str]],
        current: str,
        on_pick: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._current = current
        self._on_pick = on_pick
        self._hovered_idx = -1
        self.setMouseTracking(True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Popup
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        bw = (
            self.PADDING * 2
            + len(options) * self.BUBBLE_R * 2
            + max(0, len(options) - 1) * self.BUBBLE_GAP
        )
        bh = self.PADDING * 2 + self.BUBBLE_R * 2 + 18  # extra for title row
        self.resize(bw, bh)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Pill background
        path = QPainterPath()
        path.addRoundedRect(self.rect().toRectF(), 18, 18)
        p.fillPath(path, GLASS_DARK)
        p.setPen(QPen(GLASS_RIM, 1.0))
        p.drawPath(path)

        # Title
        f = p.font()
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(255, 255, 255, 160))
        title_rect = QRect(0, 4, self.width(), 14)
        p.drawText(title_rect, int(Qt.AlignmentFlag.AlignCenter), self._title.upper())

        # Bubbles
        x = self.PADDING
        y = self.height() - self.PADDING - self.BUBBLE_R * 2
        for i, (key, label) in enumerate(self._options):
            cx = x + self.BUBBLE_R
            cy = y + self.BUBBLE_R
            is_active = key == self._current
            is_hover = i == self._hovered_idx

            if is_active:
                core = CYAN
                deep = DEEP_CYAN
            elif is_hover:
                core = QColor(255, 255, 255, 50)
                deep = QColor(8, 16, 30, 240)
            else:
                core = QColor(40, 60, 90, 220)
                deep = QColor(8, 16, 30, 240)

            # Halo on active
            if is_active:
                for r in range(6, 0, -2):
                    halo = QColor(core)
                    halo.setAlpha(28)
                    p.setBrush(halo)
                    p.setPen(Qt.PenStyle.NoPen)
                    p.drawEllipse(
                        cx - self.BUBBLE_R - r, cy - self.BUBBLE_R - r,
                        (self.BUBBLE_R + r) * 2, (self.BUBBLE_R + r) * 2,
                    )

            grad = QRadialGradient(cx - 4, cy - 6, self.BUBBLE_R * 1.4)
            grad.setColorAt(0.0, _bright(core, 30) if is_active else core)
            grad.setColorAt(1.0, deep)
            p.setBrush(QBrush(grad))
            p.setPen(QPen(GLASS_RIM, 0.8))
            p.drawEllipse(
                cx - self.BUBBLE_R, cy - self.BUBBLE_R,
                self.BUBBLE_R * 2, self.BUBBLE_R * 2,
            )

            # Label
            p.setPen(NIGHT if is_active else WHITE_DIM)
            f2 = p.font()
            f2.setPointSize(9)
            f2.setBold(is_active)
            p.setFont(f2)
            r = QRect(cx - self.BUBBLE_R, cy - self.BUBBLE_R,
                       self.BUBBLE_R * 2, self.BUBBLE_R * 2)
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), label)

            x += self.BUBBLE_R * 2 + self.BUBBLE_GAP

    def mouseMoveEvent(self, e) -> None:
        self._hovered_idx = self._idx_at(e.position().x(), e.position().y())
        self.update()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e) -> None:
        idx = self._idx_at(e.position().x(), e.position().y())
        if idx >= 0:
            key = self._options[idx][0]
            self._on_pick(key)
            # v0.9.6 (TJ-feedback): don't slam the popup shut on the
            # first click — keep it open so the user can see the new
            # selection's highlight, AND change their mind if they want.
            # Update the visual selection state immediately, then
            # auto-close after a short dwell.
            self._current = key
            self.update()
            self._schedule_auto_close()

    def _schedule_auto_close(self) -> None:
        if not hasattr(self, "_auto_close_timer") or self._auto_close_timer is None:
            self._auto_close_timer = QTimer(self)
            self._auto_close_timer.setSingleShot(True)
            self._auto_close_timer.timeout.connect(self.close)
        self._auto_close_timer.start(1500)

    def leaveEvent(self, _e) -> None:  # noqa: N802 — Qt name
        # User moved the mouse away — start a faster close timer (they're
        # done with the picker). Don't close immediately so a slight
        # hover-out doesn't punish them.
        if not hasattr(self, "_leave_close_timer") or self._leave_close_timer is None:
            self._leave_close_timer = QTimer(self)
            self._leave_close_timer.setSingleShot(True)
            self._leave_close_timer.timeout.connect(self.close)
        self._leave_close_timer.start(800)

    def enterEvent(self, _e) -> None:  # noqa: N802
        # User came back — cancel any pending close.
        if hasattr(self, "_leave_close_timer") and self._leave_close_timer is not None:
            self._leave_close_timer.stop()

    def _idx_at(self, x: float, y: float) -> int:
        bx = self.PADDING
        by = self.height() - self.PADDING - self.BUBBLE_R * 2
        for i in range(len(self._options)):
            cx = bx + self.BUBBLE_R
            cy = by + self.BUBBLE_R
            if math.hypot(x - cx, y - cy) <= self.BUBBLE_R:
                return i
            bx += self.BUBBLE_R * 2 + self.BUBBLE_GAP
        return -1


def _bright(c: QColor, by: int) -> QColor:
    return QColor(
        min(255, c.red() + by),
        min(255, c.green() + by),
        min(255, c.blue() + by),
        c.alpha(),
    )


# Idle palette — neutral dim grey so the orb disappears into the background
# until the user hovers it. Voicely-tier minimalism.
_DIM_DOT = QColor(64, 78, 96, 200)
_DIM_DOT_DEEP = QColor(20, 30, 44, 220)


def _blend(a: QColor, b: QColor, t: float) -> QColor:
    """Linearly interpolate two QColors. t=0 → a, t=1 → b."""
    t = max(0.0, min(1.0, t))
    return QColor(
        int(a.red() * (1 - t) + b.red() * t),
        int(a.green() * (1 - t) + b.green() * t),
        int(a.blue() * (1 - t) + b.blue() * t),
        int(a.alpha() * (1 - t) + b.alpha() * t),
    )
