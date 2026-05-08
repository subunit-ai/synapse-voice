"""Audio capture via sounddevice."""
from __future__ import annotations

import threading
import wave
from pathlib import Path
from typing import Optional

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000  # Whisper expects 16kHz mono


class Recorder:
    def __init__(self, sample_rate: int = SAMPLE_RATE) -> None:
        self.sample_rate = sample_rate
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._recording = False
        self._latest_level = 0.0  # RMS of most recent chunk, 0..1

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def level(self) -> float:
        """RMS amplitude of the latest captured chunk, 0.0..1.0."""
        return self._latest_level

    def _callback(self, indata, frames, time, status) -> None:
        if status:
            print(f"[recorder] status: {status}", flush=True)
        with self._lock:
            self._chunks.append(indata.copy())
        rms = float(np.sqrt(np.mean(indata.astype(np.float32) ** 2)))
        self._latest_level = min(1.0, rms * 4.0)  # ~4x boost for visual punch

    def start(self) -> None:
        if self._recording:
            return
        with self._lock:
            self._chunks = []
        self._stream = sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()
        self._recording = True

    def stop(self) -> np.ndarray:
        if not self._recording:
            return np.zeros(0, dtype=np.float32)
        # try/finally so a failed stream-close doesn't leave _recording stuck
        # at True (otherwise the next hotkey press toggles us into a "stop"
        # path that immediately yields zero audio → user sees a flash bubble
        # and nothing transcribes).
        try:
            if self._stream is not None:
                try:
                    self._stream.stop()
                except Exception as e:
                    print(f"[recorder] stream.stop() failed: {e}", flush=True)
                try:
                    self._stream.close()
                except Exception as e:
                    print(f"[recorder] stream.close() failed: {e}", flush=True)
        finally:
            self._stream = None
            self._recording = False

        with self._lock:
            if not self._chunks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(self._chunks, axis=0).flatten()
            self._chunks = []
        return audio.astype(np.float32)

    def save_wav(self, audio: np.ndarray, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())
