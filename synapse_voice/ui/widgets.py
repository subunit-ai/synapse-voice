"""Custom UI widgets — animated toggle, brand logo, gradient frames."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QSize,
    Qt,
    pyqtProperty,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import QFrame, QSizePolicy, QWidget

CYAN = QColor(64, 214, 255)
NIGHT_2 = QColor(12, 24, 40)
NIGHT_BORDER = QColor(31, 49, 69)
WHITE = QColor(230, 242, 251)
WHITE_DIM = QColor(159, 177, 189)


def _logo_candidates() -> list[Path]:
    """Where the brand logo might live — tries dev paths and PyInstaller bundle."""
    import sys

    candidates: list[Path] = []
    # PyInstaller frozen app: data files end up under sys._MEIPASS/icons/
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "icons" / "subunit-logo.png")
    # Dev / source layout
    here = Path(__file__).resolve()
    candidates.extend([
        here.parent.parent.parent / "icons" / "subunit-logo.png",
        here.parent.parent / "icons" / "subunit-logo.png",
    ])
    return candidates


class AnimatedToggle(QWidget):
    """iOS-style switch with a smooth slide animation.

    Drop-in replacement for QCheckBox in our Settings UI — emits `toggled`
    with the new state. Hold the value with `isChecked()` / `setChecked()`.
    """

    toggled = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None, checked: bool = False) -> None:
        super().__init__(parent)
        self._checked = bool(checked)
        self._track_radius = 11
        self._thumb_offset = float(20 if self._checked else 2)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setMinimumSize(QSize(44, 24))
        self.setMaximumSize(QSize(44, 24))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._anim = QPropertyAnimation(self, b"thumbOffset", self)
        self._anim.setDuration(180)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        if bool(value) == self._checked:
            return
        self._checked = bool(value)
        self._anim.stop()
        self._anim.setStartValue(self._thumb_offset)
        self._anim.setEndValue(20.0 if self._checked else 2.0)
        self._anim.start()
        self.toggled.emit(self._checked)
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
        super().mousePressEvent(e)

    @pyqtProperty(float)
    def thumbOffset(self) -> float:  # type: ignore[override]
        return self._thumb_offset

    @thumbOffset.setter  # type: ignore[no-redef]
    def thumbOffset(self, v: float) -> None:
        self._thumb_offset = v
        self.update()

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Track
        p.setPen(Qt.PenStyle.NoPen)
        if self._checked:
            track = QColor(CYAN)
        else:
            track = QColor(NIGHT_BORDER)
            track.setAlpha(180)
        p.setBrush(track)
        p.drawRoundedRect(0, 1, 44, 22, self._track_radius, self._track_radius)

        # Thumb
        thumb = QColor(WHITE) if self._checked else QColor(WHITE_DIM)
        p.setBrush(thumb)
        p.drawEllipse(int(self._thumb_offset), 3, 18, 18)


class BrandLogo(QWidget):
    """Renders the subunit logo tinted in our cyan brand colour."""

    def __init__(
        self,
        size: int = 48,
        color: QColor = CYAN,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._size = size
        self._color = color
        self.setFixedSize(QSize(size, size))
        self._pixmap: Optional[QPixmap] = self._load_pixmap()

    def _load_pixmap(self) -> Optional[QPixmap]:
        # Look for the logo bundled with the app. Pre-scale at 2x the display
        # size for crisp rendering on hi-DPI screens (Qt's drawPixmap then
        # downsamples with SmoothPixmapTransform).
        candidates = _logo_candidates()
        for path in candidates:
            if path.exists():
                pix = QPixmap(str(path))
                if not pix.isNull():
                    target = max(self._size * 2, 96)
                    return pix.scaled(
                        target,
                        target,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
        return None

    def paintEvent(self, _e: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._pixmap is None:
            # Fallback: cyan filled diamond
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self._color)
            r = self._size // 2
            cx = cy = self._size // 2
            from PyQt6.QtCore import QPointF
            from PyQt6.QtGui import QPolygonF

            p.drawPolygon(
                QPolygonF([
                    QPointF(cx, cy - r),
                    QPointF(cx + r, cy),
                    QPointF(cx, cy + r),
                    QPointF(cx - r, cy),
                ])
            )
            return
        # Tint a copy of the high-res pixmap, then scale into the widget rect.
        # Qt's SmoothPixmapTransform hint gives a crisper result than the
        # pre-scaled approach because the pixmap is still oversampled here.
        p.drawPixmap(self.rect(), _tint_pixmap(self._pixmap, self._color))


def _tint_pixmap(src: QPixmap, color: QColor) -> QPixmap:
    """Return a copy of `src` tinted entirely to `color`, preserving alpha."""
    out = QPixmap(src.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.drawPixmap(0, 0, src)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    p.fillRect(out.rect(), color)
    p.end()
    return out


def make_logo_pixmap(size: int = 256, color: QColor = CYAN) -> QPixmap:
    """Render a tinted brand-logo pixmap (used for tray + window icons)."""
    candidates = _logo_candidates()
    base: Optional[QPixmap] = None
    for path in candidates:
        if path.exists():
            base = QPixmap(str(path))
            if not base.isNull():
                base = base.scaled(
                    size,
                    size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                break
    out = QPixmap(size, size)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if base is not None:
        tinted = _tint_pixmap(base, color)
        x = (size - tinted.width()) // 2
        y = (size - tinted.height()) // 2
        p.drawPixmap(x, y, tinted)
    else:
        # Fallback diamond
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        r = size // 2 - 8
        cx = cy = size // 2
        from PyQt6.QtCore import QPointF
        from PyQt6.QtGui import QPolygonF

        p.drawPolygon(
            QPolygonF([
                QPointF(cx, cy - r),
                QPointF(cx + r, cy),
                QPointF(cx, cy + r),
                QPointF(cx - r, cy),
            ])
        )
    p.end()
    return out


class Card(QFrame):
    """Glassy card with a soft cyan border."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
