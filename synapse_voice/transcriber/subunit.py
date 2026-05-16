"""Subunit-server transcription endpoint (Phase 3 — DSGVO premium)."""
from __future__ import annotations

import io
import wave

import numpy as np
import requests

from .base import TranscriberError, TrialExpiredError

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
    def __init__(
        self,
        endpoint: str,
        api_key: str = "",
        bearer_token: str = "",
        quality_mode: str = "quality",
    ) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        # v0.9.5 (2026-05-16): support Subunit-Account Bearer tokens from
        # auth.subunit.ai. When set, Bearer takes precedence over the
        # legacy X-API-Key. The transcribe-server will accept either
        # during the migration window.
        self.bearer_token = bearer_token
        # 2026-05-16: "auto" / "quality" / "fast". Sent as a form field;
        # server falls back to its default if it doesn't recognise the
        # value (older transcribe-server builds).
        self.quality_mode = (quality_mode or "auto").lower()
        # Set by base.get_transcriber() from the user's vocab list. Mirrors
        # the cloud + local providers so Whisper biases toward custom names
        # / jargon on the Subunit backend too.
        self.initial_prompt: str = ""

    def transcribe(self, audio: np.ndarray, language: str = "de") -> str:
        if audio.size == 0:
            return ""
        wav_bytes = _audio_to_wav_bytes(audio)
        files = {"file": ("audio.wav", wav_bytes, "audio/wav")}
        data = {"language": language, "quality_mode": self.quality_mode}
        if self.initial_prompt:
            data["prompt"] = self.initial_prompt
        if self.bearer_token:
            headers = {"Authorization": f"Bearer {self.bearer_token}"}
        elif self.api_key:
            headers = {"X-API-Key": self.api_key}
        else:
            headers = {}
        try:
            r = requests.post(
                self.endpoint, headers=headers, files=files, data=data, timeout=60
            )
            if r.status_code == 402:
                # Trial expired or no Pro subscription. Bubble a typed
                # error so the UI can show the paywall instead of a
                # generic "transcription failed" toast.
                raise TrialExpiredError(
                    "Free trial ended — please upgrade to keep using the Subunit cloud."
                )
            r.raise_for_status()
        except TrialExpiredError:
            raise
        except requests.HTTPError as e:
            raise TranscriberError(f"Subunit HTTP {r.status_code}: {r.text[:200]}") from e
        except requests.RequestException as e:
            raise TranscriberError(f"Subunit request failed: {e}") from e
        try:
            return r.json().get("text", "").strip()
        except ValueError as e:
            raise TranscriberError(f"Subunit non-JSON: {r.text[:200]}") from e
