"""Diarization client — POSTs WAV bytes to transcribe.subunit.ai /v1/diarize.

The heavy diarization model (silero-VAD + WeSpeaker ResNet34-LM + spectral
clustering, ~250MB of ONNX + 600MB torch) lives on the server. The client
sends WAV bytes, receives a list of speaker-tagged time ranges, and merges
those with the Whisper transcript segments locally.

Returns ``None`` quietly on any error so the caller can fall back to the
non-diarized transcript — this is best-effort, never blocks the user.
"""
from __future__ import annotations

import io
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import requests

from .logger import get as _get_logger

_log = _get_logger(__name__)


@dataclass
class SpeakerSegment:
    start_s: float
    end_s: float
    speaker: str

    @classmethod
    def from_dict(cls, d: dict) -> "SpeakerSegment":
        return cls(
            start_s=float(d.get("start_s", 0.0)),
            end_s=float(d.get("end_s", 0.0)),
            speaker=str(d.get("speaker", "?")),
        )


def _diarize_url(transcribe_endpoint: str) -> str:
    base = transcribe_endpoint.rstrip("/")
    for marker in ("/v1/transcribe", "/v1"):
        if base.endswith(marker):
            base = base[: -len(marker)]
            break
    return f"{base.rstrip('/')}/v1/diarize"


def diarize_audio(
    audio_path: Path | str,
    transcribe_endpoint: str,
    api_key: str,
    *,
    num_speakers: int | None = None,
    max_speakers: int = 8,
    timeout: float = 120.0,
) -> list[SpeakerSegment] | None:
    """POST a WAV file to /v1/diarize, return the speaker segments.

    Returns ``None`` on any error (network, auth, server, parse).
    """
    if not transcribe_endpoint or not api_key:
        return None
    url = _diarize_url(transcribe_endpoint)
    headers = {"X-API-Key": api_key}
    try:
        with open(audio_path, "rb") as f:
            files = {"file": (Path(audio_path).name, f, "audio/wav")}
            data: dict[str, object] = {"max_speakers": max_speakers}
            if num_speakers is not None:
                data["num_speakers"] = num_speakers
            r = requests.post(url, headers=headers, files=files, data=data, timeout=timeout)
        if r.status_code >= 400:
            _log.warning("Diarize HTTP %s: %s", r.status_code, r.text[:200])
            return None
        payload = r.json()
        segments = payload.get("segments") or []
        return [SpeakerSegment.from_dict(s) for s in segments]
    except (OSError, requests.RequestException, ValueError) as e:
        _log.warning("Diarize request failed: %s", e)
        return None


def diarize_pcm(
    samples: np.ndarray,
    sample_rate: int,
    transcribe_endpoint: str,
    api_key: str,
    **kwargs,
) -> list[SpeakerSegment] | None:
    """Like :func:`diarize_audio` but takes in-memory PCM samples.

    The samples must be 1D ``float32`` in [-1, 1] or ``int16``. We write
    them to an in-memory WAV buffer and POST that to the server.
    """
    if samples is None or samples.size == 0:
        return None
    # Convert to int16 if necessary.
    if samples.dtype == np.float32 or samples.dtype == np.float64:
        clipped = np.clip(samples, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype(np.int16)
    else:
        pcm = samples.astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    buf.seek(0)

    url = _diarize_url(transcribe_endpoint)
    headers = {"X-API-Key": api_key}
    try:
        files = {"file": ("recording.wav", buf, "audio/wav")}
        data: dict[str, object] = {"max_speakers": kwargs.get("max_speakers", 8)}
        if kwargs.get("num_speakers") is not None:
            data["num_speakers"] = kwargs["num_speakers"]
        r = requests.post(url, headers=headers, files=files, data=data, timeout=kwargs.get("timeout", 120.0))
        if r.status_code >= 400:
            _log.warning("Diarize HTTP %s: %s", r.status_code, r.text[:200])
            return None
        payload = r.json()
        return [SpeakerSegment.from_dict(s) for s in payload.get("segments") or []]
    except (requests.RequestException, ValueError) as e:
        _log.warning("Diarize request failed: %s", e)
        return None


# ── Whisper-segment ↔ Speaker-segment merge ────────────────────────────────


@dataclass
class TranscriptWithSpeaker:
    """A single line of speaker-tagged transcript."""
    speaker: str
    text: str
    start_s: float
    end_s: float


def merge_whisper_with_speakers(
    whisper_segments: Iterable[dict],
    speaker_segments: list[SpeakerSegment],
) -> list[TranscriptWithSpeaker]:
    """Match each Whisper segment to the dominant speaker in its time range.

    `whisper_segments` is the standard faster-whisper output:
    ``[{"start": float, "end": float, "text": str}, ...]``.

    We pick the speaker whose total overlap with the Whisper segment is
    largest. Whisper segments that don't overlap any speaker fall back to
    speaker="?" rather than being dropped.
    """
    out: list[TranscriptWithSpeaker] = []
    if not speaker_segments:
        for seg in whisper_segments:
            out.append(TranscriptWithSpeaker(
                speaker="?",
                text=str(seg.get("text", "")).strip(),
                start_s=float(seg.get("start", 0.0)),
                end_s=float(seg.get("end", 0.0)),
            ))
        return out

    for seg in whisper_segments:
        ws_start = float(seg.get("start", 0.0))
        ws_end = float(seg.get("end", 0.0))
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        best_speaker = "?"
        best_overlap = 0.0
        for sp in speaker_segments:
            overlap = min(ws_end, sp.end_s) - max(ws_start, sp.start_s)
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = sp.speaker
        out.append(TranscriptWithSpeaker(
            speaker=best_speaker,
            text=text,
            start_s=ws_start,
            end_s=ws_end,
        ))
    return out


def format_speaker_transcript(lines: list[TranscriptWithSpeaker]) -> str:
    """Render ``[Speaker]: text`` lines, collapsing consecutive same-speaker turns."""
    if not lines:
        return ""
    out_lines: list[str] = []
    current_speaker: str | None = None
    current_text: list[str] = []

    def flush() -> None:
        if current_speaker is not None and current_text:
            text = " ".join(current_text).strip()
            if text:
                out_lines.append(f"{current_speaker}: {text}")

    for ln in lines:
        if ln.speaker == current_speaker:
            current_text.append(ln.text)
        else:
            flush()
            current_speaker = ln.speaker
            current_text = [ln.text]
    flush()
    return "\n\n".join(out_lines)
