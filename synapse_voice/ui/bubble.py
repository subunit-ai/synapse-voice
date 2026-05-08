"""Floating indicator bubble shown near cursor during recording / processing.

Phase 2: smooth fade-in/out, audio-level waveform during recording, brand-styled.
"""
from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    Qt,
    QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QWidget

CYAN = QColor(64, 214, 255)
NIGHT = QColor(2, 8, 23, 240)
NIGHT_BORDER = QColor(31, 49, 69, 200)
WHITE = QColor(255, 255, 255)
WHITE_DIM = QColor(255, 255, 255, 180)
RED = QColor(255, 88, 92)
GREEN = QColor(80, 220, 130)
YELLOW = QColor(255, 196, 80)

STATE_ACCENTS = {
    "idle": WHITE_DIM,
    "recording": RED,
    "transcribing": CYAN,
    "done": GREEN,
    "error": YELLOW,
}


class Bubble(QWidget):
    BUBBLE_HEIGHT = 52
    BUBBLE_RADIUS = 18
    GLOW_PADDING = 14  # extra space around the bubble for the glow halo
    METER_BARS = 18  # waveform bar count

    def __init__(self) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        self._state = "idle"
        self._text = ""
        self._level_provider: Optional[Callable[[], float]] = None
        self._meter_history: list[float] = [0.0] * self.METER_BARS
        self._pulse_phase = 0.0

        # Opacity effect for fade animations
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_anim.setDuration(160)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        # Driver tick — drives waveform shift + pulse during recording/transcribing
        self._tick = QTimer(self)
        self._tick.setInterval(50)  # 20fps
        self._tick.timeout.connect(self._on_tick)

        # Auto-hide timer for terminal states
        self._auto_hide = QTimer(self)
        self._auto_hide.setSingleShot(True)
        self._auto_hide.timeout.connect(self.fade_out)

        self._font = QFont("Inter", 10)
        if not self._font.exactMatch():
            self._font = QFont()
            self._font.setPointSize(10)
        self._font.setWeight(QFont.Weight.Medium)

        self.resize(280, self.BUBBLE_HEIGHT)

    def set_level_provider(self, provider: Optional[Callable[[], float]]) -> None:
        """Set a callable returning the current 0..1 audio level (used during recording)."""
        self._level_provider = provider

    def show_state(
        self,
        state: str,
        text: str,
        auto_hide_ms: int = 0,
        anchor_to_cursor: bool = True,
    ) -> None:
        # Cancel any in-flight fade or auto-hide before transitioning. Without
        # this, calling show_state during the previous fade_out leaves the
        # bubble half-visible / partly-hidden and made repeated triggers fail.
        self._fade_anim.stop()
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._auto_hide.stop()

        self._state = state
        self._text = text
        self._meter_history = [0.0] * self.METER_BARS
        self._pulse_phase = 0.0
        self._reposition_for_text(text, anchor_to_cursor)

        if state in ("recording", "transcribing"):
            if not self._tick.isActive():
                self._tick.start()
        else:
            self._tick.stop()

        if not self.isVisible():
            self._opacity_effect.setOpacity(0.0)
            self.show()
        self.fade_in()
        self.update()

        if auto_hide_ms > 0:
            self._auto_hide.start(auto_hide_ms)

    def fade_in(self) -> None:
        self._fade_anim.stop()
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.setDuration(220)
        self._fade_anim.start()

    def fade_out(self) -> None:
        self._fade_anim.stop()
        self._fade_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setDuration(360)
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._fade_anim.finished.connect(self._after_fade_out)
        self._fade_anim.start()

    def _after_fade_out(self) -> None:
        self._tick.stop()
        self.hide()

    def _reposition_for_text(self, text: str, anchor_to_cursor: bool) -> None:
        # measure with current font instead of guessing
        from PyQt6.QtGui import QFontMetrics
        from PyQt6.QtWidgets import QApplication

        fm = QFontMetrics(self._font)
        text_w = fm.horizontalAdvance(text) + 12
        meter_width = 110 if self._state in ("recording", "transcribing") else 0
        # rail(6) + dot(8+8) + gap + meter + text + right padding(14) + glow padding x2
        inner_w = max(220, min(520, 50 + meter_width + text_w))
        width = inner_w + self.GLOW_PADDING * 2
        height = self.BUBBLE_HEIGHT + self.GLOW_PADDING * 2
        self.resize(width, height)

        # Position at bottom-center of the screen the cursor is on (Wispr-Flow style).
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        rect = screen.availableGeometry()
        x = rect.x() + (rect.width() - width) // 2
        y = rect.y() + rect.height() - height - 70
        self.move(x, y)

    def _on_tick(self) -> None:
        # Always advance pulse phase for the breathing dot
        import math

        self._pulse_phase += 0.18
        if self._state == "recording" and self._level_provider is not None:
            level = float(self._level_provider())
            self._meter_history.pop(0)
            self._meter_history.append(level)
        elif self._state == "transcribing":
            # cosmetic dancing waveform until result arrives
            self._meter_history = [
                0.25 + 0.45 * (0.5 + 0.5 * math.sin(self._pulse_phase + i * 0.5))
                for i in range(self.METER_BARS)
            ]
        self.update()

    def paintEvent(self, _event) -> None:
        import math

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Inner pill rect (the bubble itself), inset by GLOW_PADDING for the halo.
        gp = self.GLOW_PADDING
        inner = self.rect().adjusted(gp, gp, -gp, -gp)

        accent = STATE_ACCENTS.get(self._state, CYAN)

        # Soft glow halo — multi-pass rounded rects with diminishing alpha.
        # During recording the glow breathes with the pulse phase.
        glow_strength = 1.0
        if self._state == "recording":
            glow_strength = 0.85 + 0.25 * (0.5 + 0.5 * math.sin(self._pulse_phase * 0.6))
        for i in range(gp, 0, -2):
            alpha = int(28 * glow_strength * (1 - i / gp))
            if alpha <= 0:
                continue
            halo = QColor(accent)
            halo.setAlpha(alpha)
            halo_rect = inner.adjusted(-i, -i, i, i)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawRoundedRect(halo_rect, self.BUBBLE_RADIUS + i, self.BUBBLE_RADIUS + i)

        # Background pill
        path = QPainterPath()
        path.addRoundedRect(inner.toRectF(), self.BUBBLE_RADIUS, self.BUBBLE_RADIUS)
        p.fillPath(path, NIGHT)
        p.setPen(QPen(NIGHT_BORDER, 1.0))
        p.drawPath(path)

        # Accent left rail (thin)
        p.fillRect(inner.x() + 2, inner.y() + 2, 3, inner.height() - 4, accent)

        # State icon — pulsing colored dot during recording / transcribing
        dot_size = 10
        icon_x = inner.x() + 16
        icon_y = inner.y() + inner.height() // 2 - dot_size // 2
        if self._state in ("recording", "transcribing"):
            # outer breathing ring
            ring_size = int(
                dot_size + 6 + 4 * (0.5 + 0.5 * math.sin(self._pulse_phase * 0.9))
            )
            ring = QColor(accent)
            ring.setAlpha(70)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ring)
            p.drawEllipse(
                icon_x - (ring_size - dot_size) // 2,
                icon_y - (ring_size - dot_size) // 2,
                ring_size,
                ring_size,
            )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(accent)
        p.drawEllipse(icon_x, icon_y, dot_size, dot_size)

        text_x = icon_x + dot_size + 8
        # waveform meter (only during recording / transcribing)
        if self._state in ("recording", "transcribing"):
            meter_x = text_x
            meter_w = 100
            self._draw_meter(
                p,
                meter_x,
                inner.y() + 10,
                meter_w,
                inner.height() - 20,
                accent,
            )
            text_x = meter_x + meter_w + 10

        # text
        p.setPen(WHITE)
        p.setFont(self._font)
        text_rect = inner.adjusted(text_x - inner.x(), 0, -10, 0)
        p.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._text,
        )

    def _draw_meter(
        self, p: QPainter, x: int, y: int, w: int, h: int, color: QColor
    ) -> None:
        n = len(self._meter_history)
        if n == 0:
            return
        gap = 2
        bar_w = max(1, (w - gap * (n - 1)) // n)
        for i, lvl in enumerate(self._meter_history):
            bar_h = max(2, int(h * min(1.0, lvl)))
            bx = x + i * (bar_w + gap)
            by = y + (h - bar_h) // 2
            faded = QColor(color)
            faded.setAlpha(int(120 + 135 * min(1.0, lvl)))
            p.fillRect(bx, by, bar_w, bar_h, faded)
