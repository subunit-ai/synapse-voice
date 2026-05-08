"""Local transcription via faster-whisper."""
from __future__ import annotations

import threading
from typing import Optional

import numpy as np

from .base import TranscriberError


class LocalTranscriber:
    def __init__(
        self,
        model: str = "base",
        device: str = "auto",
        initial_prompt: str = "",
    ) -> None:
        self.model_name = model
        self.device = device
        # initial_prompt nudges Whisper toward known terms — Lexikon entries
        # ("write_as" values) are concatenated and passed here so domain
        # vocabulary (brand names, technical terms) is biased correctly.
        self.initial_prompt = initial_prompt
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise TranscriberError("faster-whisper not installed") from e
        device = self.device
        compute_type = "float16" if device == "cuda" else "int8"
        if device == "auto":
            try:
                import torch

                if torch.cuda.is_available():
                    device, compute_type = "cuda", "float16"
                else:
                    device, compute_type = "cpu", "int8"
            except ImportError:
                device, compute_type = "cpu", "int8"
        self._model = WhisperModel(self.model_name, device=device, compute_type=compute_type)
        return self._model

    def transcribe(self, audio: np.ndarray, language: str = "de") -> str:
        if audio.size == 0:
            return ""
        with self._lock:
            model = self._load()
            kwargs = dict(
                language=language,
                beam_size=5,
                vad_filter=True,
            )
            if self.initial_prompt:
                kwargs["initial_prompt"] = self.initial_prompt
            segments, _info = model.transcribe(audio, **kwargs)
            return " ".join(seg.text.strip() for seg in segments).strip()
