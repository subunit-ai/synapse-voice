"""Local transcription via onnx-asr — Whisper running on raw onnxruntime.

This is the local backend used when faster-whisper / ctranslate2 isn't
available for the host architecture.  Specifically: Windows-on-ARM64,
where ctranslate2 has no wheels (Linux + macOS ARM64 only as of Q2 2026).

The onnx-asr library is a thin Python wrapper that:
  * pulls Whisper ONNX models from HF Hub on first use (cached locally)
  * runs encoder + decoder via onnxruntime (which DOES ship Win-ARM64
    wheels)
  * implements the log-mel preprocessor + greedy decoder in pure numpy

We map our existing `local_model` config values (the faster-whisper model
names like ``base`` / ``large-v3-turbo``) to the equivalent ``onnx-community``
HF repos so a config edit on x64 transplants cleanly to ARM and back.
"""
from __future__ import annotations

import threading

import numpy as np

from .base import TranscriberError


# Mapping from faster-whisper model name → onnx-community HF repo.  Both
# encoder and decoder live in the same repo; onnx-asr handles download
# and quantized-variant selection internally.
_MODEL_REPO = {
    "tiny": "onnx-community/whisper-tiny",
    "tiny.en": "onnx-community/whisper-tiny.en",
    "base": "onnx-community/whisper-base",
    "base.en": "onnx-community/whisper-base.en",
    "small": "onnx-community/whisper-small",
    "small.en": "onnx-community/whisper-small.en",
    "medium": "onnx-community/whisper-medium",
    "medium.en": "onnx-community/whisper-medium.en",
    "large-v2": "onnx-community/whisper-large-v2",
    "large-v3": "onnx-community/whisper-large-v3",
    "large-v3-turbo": "onnx-community/whisper-large-v3-turbo",
    "turbo": "onnx-community/whisper-large-v3-turbo",
}


def _resolve_repo(model_name: str) -> str:
    """Map a config model name to the onnx-community HF repo.  Falls back
    to the literal name (so users can also paste an HF repo path directly
    in the settings)."""
    if model_name in _MODEL_REPO:
        return _MODEL_REPO[model_name]
    if "/" in model_name:
        return model_name
    # Default to base — same fall-back behaviour as faster-whisper.
    return _MODEL_REPO["base"]


class OnnxLocalTranscriber:
    """Drop-in replacement for ``LocalTranscriber`` that runs Whisper via
    onnxruntime.  Same public API: ``transcribe(audio, language)``."""

    def __init__(
        self,
        model: str = "base",
        device: str = "auto",
        initial_prompt: str = "",
    ) -> None:
        self.model_name = model
        self.device = device  # accepted for API parity; onnx-asr picks providers automatically
        self.initial_prompt = initial_prompt
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        # Catch *anything* on import — not just ImportError.  An ARM64
        # bundle can technically include onnx_asr's .py source but still
        # fail at import time if a transitive dep (e.g. onnxruntime
        # provider DLL) is missing or the wrong arch.  Logging the real
        # exception lets us debug from the user's log instead of seeing
        # the same generic "not installed" message regardless of cause.
        try:
            import onnx_asr  # type: ignore[import-not-found]
        except ImportError as e:
            from ..logger import get as _get_logger
            _get_logger(__name__).error("onnx_asr ImportError: %s", e)
            raise TranscriberError(
                f"onnx-asr import failed (ImportError): {e}"
            ) from e
        except BaseException as e:
            from ..logger import get as _get_logger
            _get_logger(__name__).error(
                "onnx_asr load failed (%s): %s", type(e).__name__, e
            )
            raise TranscriberError(
                f"onnx-asr load failed ({type(e).__name__}): {e}"
            ) from e

        repo = _resolve_repo(self.model_name)
        # onnx-asr.load_model fetches the repo on first call (or reads
        # the existing HF cache).  It selects the encoder + decoder ONNX
        # files automatically from the repo layout.
        try:
            self._model = onnx_asr.load_model(repo)
        except Exception as e:  # noqa: BLE001
            from ..logger import get as _get_logger
            _get_logger(__name__).error(
                "onnx_asr.load_model(%r) failed (%s): %s",
                repo, type(e).__name__, e,
            )
            raise TranscriberError(
                f"Failed to load Whisper ONNX model {repo!r}: {e}"
            ) from e
        return self._model

    def transcribe(self, audio: np.ndarray, language: str = "de") -> str:
        if audio.size == 0:
            return ""
        # onnx-asr expects mono float32 in [-1, 1] at 16 kHz, which is
        # exactly what Recorder produces — no resampling needed.
        with self._lock:
            model = self._load()
            kwargs: dict = {}
            # Whisper ONNX models accept a `language` hint as a tokenizer
            # prompt; passing through whatever onnx-asr exposes.
            try:
                # Most onnx-asr Whisper adapters accept `language=` directly.
                result = model.recognize(audio, sample_rate=16000, language=language)
            except TypeError:
                # Older/newer signatures may not accept `language` — fall
                # back to default behaviour (auto-detection).
                result = model.recognize(audio, sample_rate=16000)
            if isinstance(result, (list, tuple)):
                return " ".join(str(s).strip() for s in result).strip()
            return str(result).strip()
