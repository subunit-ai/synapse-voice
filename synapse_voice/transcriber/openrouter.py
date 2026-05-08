"""OpenRouter Whisper API transcription."""
from __future__ import annotations

import io
import wave

import numpy as np
import requests

from .base import TranscriberError

OPENROUTER_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
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


class OpenRouterTranscriber:
    def __init__(self, api_key: str, model: str = "openai/whisper-large-v3") -> None:
        if not api_key:
            raise TranscriberError("OPENROUTER_API_KEY missing — open Settings to add it")
        self.api_key = api_key
        self.model = model

    def transcribe(self, audio: np.ndarray, language: str = "de") -> str:
        if audio.size == 0:
            return ""
        wav_bytes = _audio_to_wav_bytes(audio)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"model": self.model, "language": language}
        try:
            r = requests.post(OPENROUTER_URL, headers=headers, files=files, data=data, timeout=60)
            r.raise_for_status()
        except requests.HTTPError as e:
            body = r.text[:200] if r is not None else ""
            raise TranscriberError(f"OpenRouter HTTP {r.status_code}: {body}") from e
        except requests.RequestException as e:
            raise TranscriberError(f"OpenRouter request failed: {e}") from e
        try:
            return r.json().get("text", "").strip()
        except ValueError as e:
            raise TranscriberError(f"OpenRouter returned non-JSON: {r.text[:200]}") from e
