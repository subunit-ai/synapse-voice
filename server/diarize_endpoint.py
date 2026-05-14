"""Diarization endpoint — speaker labelling for long-form recordings.

Wraps the `diarize` PyPI package (FoxNoseTech/diarize, Apache 2.0):
silero-VAD → wespeaker_resnet34_LM embeddings → GMM-BIC speaker count → spectral
clustering. CPU-only, ~8x realtime on a single thread.

Called by Sonar after a long-form recording (>=240s) has been transcribed.
Sonar uploads the same WAV separately so the client can run transcribe + diarize
in parallel and merge segments locally.

The endpoint is intentionally simple: WAV in → JSON array of
`{start_s, end_s, speaker}` segments out. The merge with Whisper segments
happens client-side in `synapse_voice/diarization.py`.
"""
from __future__ import annotations

import io
import os
import tempfile
import time
from pathlib import Path

import soundfile as sf
import numpy as np


def diarize_audio_bytes(audio_bytes: bytes, *, num_speakers: int | None = None,
                        max_speakers: int = 8) -> dict:
    """Run diarization on the in-memory WAV bytes.

    Returns a dict with ``segments`` (list of {start_s, end_s, speaker}),
    ``num_speakers``, and ``elapsed_s``.
    """
    # diarize accepts a file path, so we materialise the bytes to a temp WAV.
    # delete=False so we can close the handle before passing to diarize.
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = Path(tmp.name)
    try:
        from diarize import diarize  # local import — heavy deps

        t0 = time.time()
        result = diarize(
            str(tmp_path),
            max_speakers=max_speakers,
            num_speakers=num_speakers,
        )
        elapsed = time.time() - t0

        segments = [
            {
                "start_s": float(seg.start),
                "end_s": float(seg.end),
                "speaker": str(seg.speaker),
            }
            for seg in result.segments
        ]
        return {
            "segments": segments,
            "num_speakers": int(result.num_speakers),
            "elapsed_s": round(elapsed, 3),
        }
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
