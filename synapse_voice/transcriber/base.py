"""Transcriber dispatch."""
from __future__ import annotations

from typing import Any, Optional, Protocol

import numpy as np


class TranscriberError(RuntimeError):
    pass


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, language: str = "de") -> str: ...


# Cache of (cache-key) -> transcriber instance. Crucial for the local backend:
# WhisperModel takes seconds to load + a few hundred MB of RAM/VRAM, so we
# create exactly one instance per (model, device) combination and reuse it.
_TRANSCRIBER_CACHE: dict[tuple, Any] = {}


# All supported modes — Local + Subunit are first-class (Privacy/DSGVO),
# OpenAI/Groq/Custom are opt-in cloud backends.
ALL_MODES = ("local", "subunit", "openai", "groq", "custom")
PRIMARY_MODES = ("local", "subunit")
CLOUD_MODES = ("openai", "groq", "custom")


def mode_label(mode: str) -> str:
    return {
        "local": "Local (faster-whisper)",
        "subunit": "Cloud — Subunit (DSGVO)",
        "openai": "Cloud — OpenAI Whisper",
        "groq": "Cloud — Groq (free tier, fast)",
        "custom": "Cloud — Custom OpenAI-compatible",
    }.get(mode, mode)


def _cache_key(mode: str, config) -> tuple:
    if mode == "local":
        return (mode, config.local_model, config.local_device)
    if mode == "subunit":
        return (mode, config.subunit_endpoint, getattr(config, "subunit_api_key", ""))
    if mode == "openai":
        return (mode, config.openai_api_key, config.openai_model)
    if mode == "groq":
        return (mode, config.groq_api_key, config.groq_model)
    if mode == "custom":
        return (mode, config.custom_endpoint, config.custom_api_key, config.custom_model)
    return (mode,)


def preflight_check(mode: str, config) -> Optional[str]:
    # Same migration as in get_transcriber — guard the pre-flight too.
    if mode == "openrouter":
        mode = "openai"
    """Return a user-facing message if the mode is missing required credentials.
    Returns None if the mode is ready to use."""
    if mode == "subunit" and not config.subunit_api_key:
        # Subunit can technically run without auth (server-config-dependent),
        # so don't block — let the actual call surface a 401 if needed.
        return None
    if mode == "openai" and not config.openai_api_key:
        return "OpenAI mode needs an API key. Open Settings to add it?"
    if mode == "groq" and not config.groq_api_key:
        return "Groq mode needs an API key. Open Settings to add it?"
    if mode == "custom":
        if not config.custom_endpoint:
            return "Custom mode needs an endpoint URL. Open Settings to set one?"
        if not config.custom_api_key:
            return "Custom mode needs an API key. Open Settings to add it?"
    return None


def get_transcriber(mode: str, config) -> Transcriber:
    # Defensive: Config.load() already migrates openrouter → openai on disk,
    # but if a stray code path passes the legacy mode in we still translate.
    if mode == "openrouter":
        mode = "openai"
    key = _cache_key(mode, config)
    cached = _TRANSCRIBER_CACHE.get(key)
    if cached is not None:
        return cached
    if mode == "local":
        from .local import LocalTranscriber

        inst = LocalTranscriber(model=config.local_model, device=config.local_device)
    elif mode == "subunit":
        from .subunit import SubunitTranscriber

        inst = SubunitTranscriber(
            endpoint=config.subunit_endpoint,
            api_key=getattr(config, "subunit_api_key", ""),
        )
    elif mode == "openai":
        from .cloud import CloudTranscriber, PROVIDER_PRESETS

        inst = CloudTranscriber(
            provider_name="OpenAI",
            endpoint=PROVIDER_PRESETS["openai"]["endpoint"],
            api_key=config.openai_api_key,
            model=config.openai_model or PROVIDER_PRESETS["openai"]["model"],
        )
    elif mode == "groq":
        from .cloud import CloudTranscriber, PROVIDER_PRESETS

        inst = CloudTranscriber(
            provider_name="Groq",
            endpoint=PROVIDER_PRESETS["groq"]["endpoint"],
            api_key=config.groq_api_key,
            model=config.groq_model or PROVIDER_PRESETS["groq"]["model"],
        )
    elif mode == "custom":
        from .cloud import CloudTranscriber

        inst = CloudTranscriber(
            provider_name="Custom",
            endpoint=config.custom_endpoint,
            api_key=config.custom_api_key,
            model=config.custom_model or "whisper-1",
        )
    else:
        raise TranscriberError(f"Unknown mode: {mode}")
    _TRANSCRIBER_CACHE[key] = inst
    return inst


def clear_cache() -> None:
    """Clear all cached transcribers — used after settings changes."""
    _TRANSCRIBER_CACHE.clear()
