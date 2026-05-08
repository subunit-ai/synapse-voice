"""Transcriber dispatch."""
from __future__ import annotations

from typing import Any, Protocol

import numpy as np


class TranscriberError(RuntimeError):
    pass


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, language: str = "de") -> str: ...


# Cache of (cache-key) -> transcriber instance. Crucial for the local backend:
# WhisperModel takes seconds to load + a few hundred MB of RAM/VRAM, so we
# create exactly one instance per (model, device) combination and reuse it.
_TRANSCRIBER_CACHE: dict[tuple, Any] = {}


def _cache_key(mode: str, config) -> tuple:
    if mode == "local":
        return (mode, config.local_model, config.local_device)
    if mode == "openrouter":
        return (mode, config.openrouter_api_key)
    if mode == "subunit":
        return (mode, config.subunit_endpoint, getattr(config, "subunit_api_key", ""))
    return (mode,)


def get_transcriber(mode: str, config) -> Transcriber:
    key = _cache_key(mode, config)
    cached = _TRANSCRIBER_CACHE.get(key)
    if cached is not None:
        return cached
    if mode == "local":
        from .local import LocalTranscriber

        inst = LocalTranscriber(model=config.local_model, device=config.local_device)
    elif mode == "openrouter":
        from .openrouter import OpenRouterTranscriber

        inst = OpenRouterTranscriber(api_key=config.openrouter_api_key)
    elif mode == "subunit":
        from .subunit import SubunitTranscriber

        inst = SubunitTranscriber(
            endpoint=config.subunit_endpoint,
            api_key=getattr(config, "subunit_api_key", ""),
        )
    else:
        raise TranscriberError(f"Unknown mode: {mode}")
    _TRANSCRIBER_CACHE[key] = inst
    return inst
