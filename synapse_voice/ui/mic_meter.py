"""Live microphone level meter for the Settings dialog.

Opens a short-lived sounddevice InputStream on the picked device, samples
RMS at ~30Hz, and renders a horizontal bar. Auto-stops when the widget
hides so we don't keep the mic open after the user closes Settings.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import sounddevice as sd
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

CYAN = QColor(64, 214, 255)
NIGHT_2 = QColor(12, 24, 40)
NIGHT_BORDER = QColor(31, 49, 69)
GREEN = QColor(80, 220, 130)
RED = QColor(255, 88, 92)


class MicLevelMeter(QWidget):
    """Tiny live VU-meter — green up to ~0.7, red above. Width auto-fills
    the parent's width. Height is fixed at ~22px so it tucks under the
    mic-picker dropdown without dominating the form layout.
    """

    BAR_HEIGHT = 22
    SAMPLE_RATE = 16000
    BLOCK_SIZE = 512

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._peak = 0.0
        self._stream: Optional[sd.InputStream] = None
        self._device: Optional[int] = None
        self.setMinimumHeight(self.BAR_HEIGHT)
        self.setMaximumHeight(self.BAR_HEIGHT)

        # Repaint timer — separate from the audio callback so painting
        # always runs on the GUI thread.
        self._tick = QTimer(self)
        self._tick.setInterval(33)  # ~30fps
        self._tick.timeout.connect(self._on_tick)

    def set_device(self, device_index: Optional[int]) -> None:
        """Switch the sample-source. Pass None for system default."""
        if device_index == self._device and self._stream is not None:
            return
        self._device = device_index
        self._restart_stream()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        self._restart_stream()
        self._tick.start()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        self._tick.stop()
        self._stop_stream()

    def closeEvent(self, e) -> None:
        self._tick.stop()
        self._stop_stream()
        super().closeEvent(e)

    # ── Audio plumbing ─────────────────────────────────────────────────────

    def _audio_cb(self, indata, _frames, _time, _status) -> None:
        # Fast RMS → 0..1 with a soft cap; smoothing happens in _on_tick.
        rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        self._level = min(1.0, rms * 6.0)

    def _restart_stream(self) -> None:
        self._stop_stream()
        try:
            self._stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                callback=self._audio_cb,
                blocksize=self.BLOCK_SIZE,
                device=self._device,
            )
            self._stream.start()
        except Exception:
            # If the chosen device can't be opened (busy / permission /
            # vanished), silently leave the meter showing 0. The user's
            # next pick will retry.
            self._stream = None

    def _stop_stream(self) -> None:
        if self._stream is None:
            return
        try:
            self._stream.stop()
        except Exception:
            pass
        try:
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        self._level = 0.0
        self._peak = 0.0

    # ── Render ─────────────────────────────────────────────────────────────

    def _on_tick(self) -> None:
        # Decay peak with a short tail for a natural feel
        if self._level > self._peak:
            self._peak = self._level
        else:
            self._peak = max(self._level, self._peak - 0.04)
        self.update()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        radius = 6
        # Background trough
        p.setBrush(NIGHT_2)
        p.setPen(NIGHT_BORDER)
        p.drawRoundedRect(self.rect(), radius, radius)

        # Filled bar
        fill_w = int((self.width() - 4) * min(1.0, self._level))
        if fill_w > 0:
            color = RED if self._level > 0.85 else (
                CYAN if self._level > 0.4 else GREEN
            )
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(
                2, 2, fill_w, self.height() - 4, radius - 2, radius - 2
            )
        # Peak hold marker
        if self._peak > 0.01:
            peak_x = int((self.width() - 4) * min(1.0, self._peak)) + 2
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRect(peak_x - 1, 3, 2, self.height() - 6)
