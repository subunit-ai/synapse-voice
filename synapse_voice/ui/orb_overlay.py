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
    PADDING = 38               # room around the orb for halo + satellites
    SAT_RADIUS = 9             # satellite dot radius
    SAT_DISTANCE = 26          # satellite distance from orb center

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
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        side = (self.DOT_RADIUS + self.PADDING) * 2
        self.resize(side, side)

        self._tick = QTimer(self)
        self._tick.setInterval(33)
        self._tick.timeout.connect(self._on_tick)
        self._tick.start()

        self._reposition()

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

        # Outer halo — gentle breathing pulse when idle, stronger on audio.
        # The halo size stays roughly constant; only alpha changes — so the
        # orb doesn't appear to "grow and shrink" violently.
        if self.config.orb_idle_pulse or self._state != "idle":
            breath = 0.5 + 0.5 * math.sin(self._pulse_phase * 0.7)
        else:
            breath = 0.5
        halo_strength = (
            0.55 * breath + self._level_smooth * 1.4
            if self._state != "idle"
            else 0.35 * breath
        )
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

        # Main dot — glass-morph with deep gradient
        grad = QRadialGradient(cx - 3, cy - 4, self.DOT_RADIUS * 1.4)
        grad.setColorAt(0.0, _bright(accent, 30))
        grad.setColorAt(0.6, accent)
        grad.setColorAt(1.0, accent_deep)
        p.setBrush(QBrush(grad))
        p.setPen(QPen(GLASS_RIM, 1.0))
        p.drawEllipse(
            cx - self.DOT_RADIUS, cy - self.DOT_RADIUS,
            self.DOT_RADIUS * 2, self.DOT_RADIUS * 2,
        )

        # Inner pulse — small concentric circle that breathes (signals "alive")
        inner_r = max(
            2,
            int(
                self.DOT_RADIUS * 0.35
                + 2.0 * math.sin(self._pulse_phase * 1.4)
                + self._level_smooth * (self.DOT_RADIUS * 0.5)
            ),
        )
        inner_color = QColor(255, 255, 255, 180 if self._state != "idle" else 110)
        p.setBrush(inner_color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(cx - inner_r, cy - inner_r, inner_r * 2, inner_r * 2)

        # Specular highlight
        p.setBrush(QColor(255, 255, 255, 90))
        p.drawEllipse(
            cx - int(self.DOT_RADIUS * 0.55),
            cy - int(self.DOT_RADIUS * 0.65),
            max(2, int(self.DOT_RADIUS * 0.32)),
            max(2, int(self.DOT_RADIUS * 0.32)),
        )

        # Satellite dots (faded by hover-opacity so they dissolve in/out)
        if self._satellite_opacity > 0.02:
            self._draw_satellites(p, cx, cy, accent)

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
            p.drawText(r, int(Qt.AlignmentFlag.AlignCenter),
                       (self.config.language or "DE").upper()[:2])
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
        self.update()

    def leaveEvent(self, _e) -> None:
        self._hovered = False
        self.update()

    def mousePressEvent(self, e) -> None:
        # Right-click drag = move the orb.
        if e.button() == Qt.MouseButton.RightButton:
            self._drag_origin = e.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return

        # Only react to left-clicks on satellites when they're visible.
        if self._satellite_opacity < 0.4:
            return
        cx = cy = self.DOT_RADIUS + self.PADDING
        pos = e.position()
        cx_mouse, cy_mouse = pos.x(), pos.y()
        for name, (sx, sy) in self._satellite_positions(cx, cy).items():
            if math.hypot(cx_mouse - sx, cy_mouse - sy) <= self.SAT_RADIUS + 2:
                self._handle_satellite(name, sx, sy)
                return

    def mouseMoveEvent(self, e) -> None:
        if self._drag_origin is not None:
            self.move(e.globalPosition().toPoint() - self._drag_origin)
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e) -> None:
        if self._drag_origin is not None and e.button() == Qt.MouseButton.RightButton:
            self._drag_origin = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
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
            self._open_choice_popup(
                anchor_global,
                title="Cleanup style",
                options=[("tidy", "Tidy"), ("formal", "Formal")],
                current=self.config.cleanup_style,
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
        if choice not in ("tidy", "formal"):
            return
        self.config.cleanup_style = choice
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
            self.close()

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
