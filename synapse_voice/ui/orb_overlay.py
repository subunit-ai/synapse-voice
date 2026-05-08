"""Floating Orb overlay — v0.4 killer-feature.

Always-on-top circular widget that lives at the bottom-center (or any
configurable corner) of the screen. Inside: 8 glass spheres bouncing
under Verlet integration, gently breathing while idle, expanding +
shimmering when the mic picks up audio. On hover, three satellite
buttons fan out: language picker, tonality (cleanup style), and the
local/cloud transcription mode toggle.

Replaces the simple `Bubble` notifier as the default visual feedback.
The classic Bubble stays available behind a Settings toggle for users
who prefer minimal UI.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt6.QtCore import (
    QPoint,
    QRect,
    QSize,
    Qt,
    QTimer,
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
from PyQt6.QtWidgets import QApplication, QWidget

from ..config import Config
from ..transcriber import CLOUD_MODES, mode_label

# ── Palette ────────────────────────────────────────────────────────────────
CYAN = QColor(64, 214, 255)
DEEP_CYAN = QColor(20, 96, 130)
NIGHT = QColor(2, 8, 23)
NIGHT_DIM = QColor(8, 16, 30, 220)
GLASS_HIGHLIGHT = QColor(255, 255, 255, 190)
GLASS_RIM = QColor(255, 255, 255, 90)
WHITE = QColor(255, 255, 255)
WHITE_DIM = QColor(255, 255, 255, 180)
RED = QColor(255, 88, 92)


COLOR_THEMES = {
    "cyan": (CYAN, DEEP_CYAN),
    "violet": (QColor(170, 110, 255), QColor(70, 30, 130)),
    "mint": (QColor(110, 230, 190), QColor(20, 110, 90)),
}


@dataclass
class _Sphere:
    """Verlet-integrated sphere body. Carries current + previous position so
    velocity is implicit (pos - prev_pos = velocity). Cheap, stable, no
    explicit dampening needed beyond a small velocity scaling per tick.
    """

    x: float
    y: float
    px: float
    py: float
    radius: float
    phase: float  # for shimmer / drift variation


class OrbOverlay(QWidget):
    """The persistent floating orb. State machine mirrors Bubble's: idle,
    recording, transcribing, done, error. Visual reaction differs per state:
        idle         — slow breathing pulse, spheres drift
        recording    — RED rim, spheres pushed outward by audio level
        transcribing — CYAN shimmer, spheres swirl
        done         — green flash → fade back to idle
        error        — yellow flash → fade back to idle
    """

    SPHERE_COUNT = 9
    ORB_RADIUS = 50  # the inner orb area (sphere container)
    PADDING = 22  # space around the orb for halo + satellite buttons
    BUTTON_RADIUS = 18  # satellite-button radius

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
        self._theme = "cyan"
        self.setMouseTracking(True)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # NOTE on click-through: by default the orb captures clicks so the
        # 3-button picker can react to hover/click. To prevent it from
        # blocking clicks on whatever lies behind it, we shrink the orb
        # widget to its visible rect — the user can still click "around"
        # the orb via the screen area outside our ~140px window.

        # Resize: orb area + padding * 2 for halo + satellite reach
        side = (self.ORB_RADIUS + self.PADDING) * 2
        self.resize(side, side)

        self._spheres: list[_Sphere] = self._spawn_spheres()

        self._tick = QTimer(self)
        self._tick.setInterval(33)  # ~30fps
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        # Move to default position (bottom-center of cursor's screen)
        self._reposition()

    # ── Public API ─────────────────────────────────────────────────────────

    def set_level_provider(self, provider: Optional[Callable[[], float]]) -> None:
        self._level_provider = provider

    def show_state(self, state: str, _text: str = "") -> None:
        self._state = state
        if not self.isVisible():
            self.show()
        # done/error briefly flash, then revert to idle.
        if state in ("done", "error"):
            QTimer.singleShot(900, lambda: self._maybe_reset_state(state))

    def _maybe_reset_state(self, from_state: str) -> None:
        if self._state == from_state:
            self._state = "idle"
            self.update()

    # ── Geometry / placement ───────────────────────────────────────────────

    def _reposition(self) -> None:
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        x = rect.x() + rect.width() - self.width() - 24
        y = rect.y() + rect.height() - self.height() - 24
        self.move(x, y)

    # ── Physics ────────────────────────────────────────────────────────────

    def _spawn_spheres(self) -> list[_Sphere]:
        cx = cy = self.ORB_RADIUS + self.PADDING
        spheres = []
        for i in range(self.SPHERE_COUNT):
            angle = 2 * math.pi * i / self.SPHERE_COUNT + random.random() * 0.4
            r = self.ORB_RADIUS * 0.55 * (0.6 + random.random() * 0.4)
            x = cx + math.cos(angle) * r
            y = cy + math.sin(angle) * r
            spheres.append(
                _Sphere(
                    x=x,
                    y=y,
                    px=x,
                    py=y,
                    radius=8 + random.random() * 6,
                    phase=random.random() * math.pi * 2,
                )
            )
        return spheres

    def _on_tick(self) -> None:
        self._pulse_phase += 0.06
        if self._level_provider is not None:
            try:
                self._level = float(self._level_provider())
            except Exception:
                self._level = 0.0
        # Smooth the level so spheres don't jitter on fast transients
        self._level_smooth += (self._level - self._level_smooth) * 0.25
        self._step_physics()
        self.update()

    def _step_physics(self) -> None:
        cx = cy = self.ORB_RADIUS + self.PADDING

        # Idle target radius breathes slightly; audio expands it outward.
        idle_breath = (
            self.ORB_RADIUS * 0.55
            + 2.0 * math.sin(self._pulse_phase)
        )
        audio_push = self._level_smooth * (self.ORB_RADIUS - 12)
        target_r = idle_breath + audio_push

        for s in self._spheres:
            # Verlet integration
            vx = (s.x - s.px) * 0.94
            vy = (s.y - s.py) * 0.94
            s.px, s.py = s.x, s.y

            # Soft pull toward target ring
            dx = s.x - cx
            dy = s.y - cy
            d = math.hypot(dx, dy) or 1e-6
            ndx, ndy = dx / d, dy / d
            tx = cx + ndx * target_r
            ty = cy + ndy * target_r
            ax = (tx - s.x) * 0.04
            ay = (ty - s.y) * 0.04

            # Tiny tangential drift so the cluster stirs
            ax += -ndy * 0.15 * (1 + self._level_smooth * 2)
            ay += ndx * 0.15 * (1 + self._level_smooth * 2)

            # Slight per-sphere wobble for organic feel
            ax += math.cos(self._pulse_phase * 1.3 + s.phase) * 0.08
            ay += math.sin(self._pulse_phase * 1.7 + s.phase) * 0.08

            s.x += vx + ax
            s.y += vy + ay

        # Soft inter-sphere repulsion so they don't overlap
        for i in range(len(self._spheres)):
            for j in range(i + 1, len(self._spheres)):
                a = self._spheres[i]
                b = self._spheres[j]
                dx = b.x - a.x
                dy = b.y - a.y
                d2 = dx * dx + dy * dy
                min_d = a.radius + b.radius
                if d2 < min_d * min_d:
                    d = math.sqrt(d2) or 1e-6
                    overlap = (min_d - d) * 0.5
                    nx = dx / d
                    ny = dy / d
                    a.x -= nx * overlap
                    a.y -= ny * overlap
                    b.x += nx * overlap
                    b.y += ny * overlap

        # Hard outer constraint — keep them inside the visible orb
        max_r = self.ORB_RADIUS - 4
        for s in self._spheres:
            dx = s.x - cx
            dy = s.y - cy
            d = math.hypot(dx, dy)
            if d > max_r:
                s.x = cx + dx / d * max_r
                s.y = cy + dy / d * max_r

    # ── Painting ───────────────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = cy = self.ORB_RADIUS + self.PADDING
        accent, accent_deep = COLOR_THEMES.get(self._theme, COLOR_THEMES["cyan"])
        if self._state == "recording":
            accent = RED
            accent_deep = QColor(120, 30, 40)

        # Outer halo (breathing pulse — subtle when idle, stronger on audio)
        breath = 0.6 + 0.4 * (0.5 + 0.5 * math.sin(self._pulse_phase * 0.6))
        halo_strength = (
            0.55 * breath + self._level_smooth * 1.2
            if self._state != "idle"
            else 0.45 * breath
        )
        for i in range(self.PADDING, 0, -3):
            alpha = int(36 * halo_strength * (1 - i / self.PADDING))
            if alpha <= 0:
                continue
            color = QColor(accent)
            color.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(
                int(cx - self.ORB_RADIUS - i),
                int(cy - self.ORB_RADIUS - i),
                int((self.ORB_RADIUS + i) * 2),
                int((self.ORB_RADIUS + i) * 2),
            )

        # Background glass disc
        disc_grad = QRadialGradient(cx, cy - 6, self.ORB_RADIUS)
        disc_grad.setColorAt(0.0, QColor(28, 56, 92, 230))
        disc_grad.setColorAt(0.7, QColor(8, 16, 30, 235))
        disc_grad.setColorAt(1.0, QColor(2, 8, 23, 240))
        p.setBrush(QBrush(disc_grad))
        p.setPen(QPen(GLASS_RIM, 1.2))
        p.drawEllipse(
            int(cx - self.ORB_RADIUS),
            int(cy - self.ORB_RADIUS),
            int(self.ORB_RADIUS * 2),
            int(self.ORB_RADIUS * 2),
        )

        # Glass spheres — back-to-front by y for fake depth
        for s in sorted(self._spheres, key=lambda s: s.y):
            self._draw_sphere(p, s, accent, accent_deep)

        # Top rim highlight on the disc — gives the "glass" feel
        rim_path = QPainterPath()
        rim_path.addEllipse(
            cx - self.ORB_RADIUS + 5,
            cy - self.ORB_RADIUS + 5,
            (self.ORB_RADIUS - 5) * 2,
            (self.ORB_RADIUS - 5) * 2,
        )
        p.setPen(QPen(GLASS_RIM, 0.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(rim_path)

        # Hover satellite buttons (lang / tonality / local-toggle).
        # Always rendered when hovered; hidden otherwise. Icons + labels
        # are drawn in tinted circles fanning out N / W / E.
        if self._hovered:
            self._draw_satellite_buttons(p, cx, cy, accent)

    def _draw_sphere(
        self, p: QPainter, s: _Sphere, accent: QColor, deep: QColor
    ) -> None:
        # Body gradient — "glass" with a bright top-left highlight
        grad = QRadialGradient(s.x - s.radius * 0.3, s.y - s.radius * 0.4, s.radius * 1.4)
        accent_bright = QColor(accent)
        accent_bright = QColor(
            min(255, accent.red() + 40),
            min(255, accent.green() + 40),
            min(255, accent.blue() + 40),
            230,
        )
        grad.setColorAt(0.0, accent_bright)
        grad.setColorAt(0.55, accent)
        grad.setColorAt(1.0, deep)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(GLASS_RIM, 0.6))
        p.drawEllipse(
            int(s.x - s.radius), int(s.y - s.radius), int(s.radius * 2), int(s.radius * 2)
        )

        # Specular highlight dot
        hl = GLASS_HIGHLIGHT
        p.setBrush(hl)
        p.setPen(Qt.PenStyle.NoPen)
        hl_r = max(2, int(s.radius * 0.32))
        p.drawEllipse(
            int(s.x - s.radius * 0.4),
            int(s.y - s.radius * 0.5),
            hl_r,
            hl_r,
        )

    def _draw_satellite_buttons(
        self, p: QPainter, cx: int, cy: int, accent: QColor
    ) -> None:
        r_orbit = self.ORB_RADIUS + 12
        # Three positions: top, left, right
        anchors = [
            ("top", cx, cy - r_orbit, "🔒" if self.config.mode == "local" else "☁"),
            ("left", cx - r_orbit, cy, _short_lang(self.config.language)),
            ("right", cx + r_orbit, cy, _short_style(self.config.cleanup_style)),
        ]
        for _name, bx, by, label in anchors:
            # Soft drop-shadow halo
            for i in range(6, 0, -1):
                alpha = int(32 * (1 - i / 6))
                color = QColor(accent)
                color.setAlpha(alpha)
                p.setBrush(color)
                p.setPen(Qt.PenStyle.NoPen)
                p.drawEllipse(
                    bx - self.BUTTON_RADIUS - i,
                    by - self.BUTTON_RADIUS - i,
                    (self.BUTTON_RADIUS + i) * 2,
                    (self.BUTTON_RADIUS + i) * 2,
                )
            # Button background
            grad = QRadialGradient(bx, by - 4, self.BUTTON_RADIUS)
            grad.setColorAt(0.0, QColor(40, 70, 110, 235))
            grad.setColorAt(1.0, QColor(8, 16, 30, 240))
            p.setBrush(QBrush(grad))
            p.setPen(QPen(GLASS_RIM, 0.8))
            p.drawEllipse(
                bx - self.BUTTON_RADIUS,
                by - self.BUTTON_RADIUS,
                self.BUTTON_RADIUS * 2,
                self.BUTTON_RADIUS * 2,
            )
            # Label / icon
            p.setPen(WHITE)
            p.drawText(
                QRect(
                    bx - self.BUTTON_RADIUS,
                    by - self.BUTTON_RADIUS,
                    self.BUTTON_RADIUS * 2,
                    self.BUTTON_RADIUS * 2,
                ),
                int(Qt.AlignmentFlag.AlignCenter),
                label,
            )

    # ── Mouse ──────────────────────────────────────────────────────────────

    def enterEvent(self, _e) -> None:
        self._hovered = True
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, e) -> None:
        if not self._hovered:
            return
        cx = cy = self.ORB_RADIUS + self.PADDING
        r_orbit = self.ORB_RADIUS + 12
        pos = e.position()
        click_x, click_y = pos.x(), pos.y()
        for name, bx, by in (
            ("top", cx, cy - r_orbit),
            ("left", cx - r_orbit, cy),
            ("right", cx + r_orbit, cy),
        ):
            if math.hypot(click_x - bx, click_y - by) <= self.BUTTON_RADIUS:
                self._handle_button(name)
                return
        # Click on the orb itself currently does nothing — reserved for
        # future "click to dictate" behavior. The hotkey is the primary
        # entry point for v0.4 to keep the scope tight.

    def _handle_button(self, which: str) -> None:
        if which == "top":
            # Toggle Local / last cloud mode
            if self.config.mode == "local":
                target = self.config.last_cloud_mode or "subunit"
            else:
                target = "local"
            self._on_change_mode(target)
        elif which == "left":
            self._cycle_language()
        elif which == "right":
            self._cycle_style()
        self.update()

    def _cycle_language(self) -> None:
        # Compact MVP: rotate through a short list. Full searchable picker
        # is a v0.5 follow-up so we don't block the v0.4 ship on it.
        langs = ["de", "en", "fr", "es", "it"]
        cur = self.config.language
        try:
            idx = langs.index(cur)
        except ValueError:
            idx = -1
        self.config.language = langs[(idx + 1) % len(langs)]
        self.config.save()

    def _cycle_style(self) -> None:
        styles = ["tidy", "formal"]
        cur = self.config.cleanup_style
        try:
            idx = styles.index(cur)
        except ValueError:
            idx = -1
        self.config.cleanup_style = styles[(idx + 1) % len(styles)]
        self.config.save()


def _short_lang(code: str) -> str:
    return (code or "de").upper()[:2]


def _short_style(style: str) -> str:
    return {"tidy": "T", "formal": "F"}.get(style, "T")
