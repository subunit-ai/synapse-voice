"""Subunit-server transcription endpoint (Phase 3 — DSGVO premium)."""
from __future__ import annotations

import io
import wave

import numpy as np
import requests

from .base import TranscriberError

SAMPLE_RATE = 16000


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


class SubunitTranscriber:
    def __init__(self, endpoint: str, api_key: str = "") -> None:
        self.endpoint = endpoint
        self.api_key = api_key

    def transcribe(self, audio: np.ndarray, language: str = "de") -> str:
        if audio.size == 0:
            return ""
        wav_bytes = _audio_to_wav_bytes(audio)
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"language": language}
        headers = {"X-API-Key": self.api_key} if self.api_key else {}
        try:
            r = requests.post(
                self.endpoint, headers=headers, files=files, data=data, timeout=60
            )
            r.raise_for_status()
        except requests.HTTPError as e:
            raise TranscriberError(f"Subunit HTTP {r.status_code}: {r.text[:200]}") from e
        except requests.RequestException as e:
            raise TranscriberError(f"Subunit request failed: {e}") from e
        try:
            return r.json().get("text", "").strip()
        except ValueError as e:
            raise TranscriberError(f"Subunit non-JSON: {r.text[:200]}") from e
