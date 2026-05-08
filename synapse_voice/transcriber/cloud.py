"""Generic OpenAI-compatible transcription backend.

Used by the OpenAI / Groq / Custom modes. The on-the-wire shape of the
`/v1/audio/transcriptions` endpoint is identical between OpenAI, Groq and
self-hosted compatible servers (e.g. LocalAI, vLLM).
"""
from __future__ import annotations

import io
import wave

import numpy as np
import requests

from ..logger import get as _get_logger
from .base import TranscriberError

_log = _get_logger(__name__)
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


class CloudTranscriber:
    def __init__(
        self,
        provider_name: str,
        endpoint: str,
        api_key: str,
        model: str,
    ) -> None:
        if not endpoint:
            raise TranscriberError(
                f"{provider_name} endpoint is empty — open Settings to configure it"
            )
        if not api_key:
            raise TranscriberError(
                f"{provider_name} API key missing — open Settings to add it"
            )
        if not model:
            raise TranscriberError(
                f"{provider_name} model name missing — open Settings to set one"
            )
        self.provider_name = provider_name
        self.endpoint = endpoint
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
            r = requests.post(
                self.endpoint, headers=headers, files=files, data=data, timeout=60
            )
            r.raise_for_status()
        except requests.HTTPError as e:
            body = ""
            status = "?"
            try:
                status = str(r.status_code)
                body = r.text[:300]
            except Exception:
                pass
            _log.error(
                "%s HTTP %s for %s: %s",
                self.provider_name,
                status,
                self.endpoint,
                body,
            )
            raise TranscriberError(
                f"{self.provider_name} HTTP {status}: {body}"
            ) from e
        except requests.RequestException as e:
            _log.error("%s request failed: %s", self.provider_name, e)
            raise TranscriberError(f"{self.provider_name} request failed: {e}") from e
        try:
            return r.json().get("text", "").strip()
        except ValueError as e:
            raise TranscriberError(
                f"{self.provider_name} returned non-JSON: {r.text[:200]}"
            ) from e


# Provider presets — surfaced in Settings so the user can pick "OpenAI" /
# "Groq" / "Custom" without having to remember endpoint URLs.
PROVIDER_PRESETS = {
    "openai": {
        "label": "OpenAI Whisper",
        "endpoint": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
        "key_hint": "sk-...",
        "signup_url": "https://platform.openai.com/api-keys",
    },
    "groq": {
        "label": "Groq Whisper (free tier)",
        "endpoint": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3-turbo",
        "key_hint": "gsk_...",
        "signup_url": "https://console.groq.com/keys",
    },
    "custom": {
        "label": "Custom OpenAI-compatible",
        "endpoint": "",
        "model": "whisper-1",
        "key_hint": "",
        "signup_url": "",
    },
}
