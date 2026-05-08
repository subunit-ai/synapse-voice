"""Transcriber dispatch."""
from __future__ import annotations

from typing import Protocol

import numpy as np


class TranscriberError(RuntimeError):
    pass


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, language: str = "de") -> str: ...


def get_transcriber(mode: str, config) -> Transcriber:
    if mode == "local":
        from .local import LocalTranscriber

        return LocalTranscriber(model=config.local_model, device=config.local_device)
    if mode == "openrouter":
        from .openrouter import OpenRouterTranscriber

        return OpenRouterTranscriber(api_key=config.openrouter_api_key)
    if mode == "subunit":
        from .subunit import SubunitTranscriber

        return SubunitTranscriber(
            endpoint=config.subunit_endpoint,
            api_key=getattr(config, "subunit_api_key", ""),
        )
    raise TranscriberError(f"Unknown mode: {mode}")
