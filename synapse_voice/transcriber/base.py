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


# All supported modes. Subunit + OpenAI + Groq + Custom are cloud backends;
# Local is the on-device fallback. Subunit is first among CLOUD_MODES because
# it's our DSGVO-compliant default — the UI tags it as "Recommended".
ALL_MODES = ("local", "subunit", "openai", "groq", "custom")
PRIMARY_MODES = ("local", "subunit")
CLOUD_MODES = ("subunit", "openai", "groq", "custom")


def mode_label(mode: str) -> str:
    return {
        "local": "Local (faster-whisper)",
        "subunit": "Cloud — Subunit (DSGVO)",
        "openai": "Cloud — OpenAI Whisper",
        "groq": "Cloud — Groq (free tier, fast)",
        "custom": "Cloud — Custom OpenAI-compatible",
    }.get(mode, mode)


def _vocab_prompt(config) -> str:
    """Build the Whisper initial_prompt from the user's Lexikon entries.
    Whisper biases toward terms in the prompt — passing the canonical
    spellings makes it much more likely to transcribe them correctly."""
    vocab = getattr(config, "vocabulary", None) or []
    terms = [v.get("write_as", "").strip() for v in vocab]
    return ", ".join(t for t in terms if t)


def apply_vocab_replace(text: str, config) -> str:
    """Post-process: literal-replace any Lexikon `sounds_like` matches with
    the canonical `write_as`. Case-insensitive, word-boundary-aware so we
    don't replace inside other words. Runs after transcription + cleanup."""
    import re

    vocab = getattr(config, "vocabulary", None) or []
    out = text
    for entry in vocab:
        sounds = (entry.get("sounds_like") or "").strip()
        canon = (entry.get("write_as") or "").strip()
        if not sounds or not canon:
            continue
        # Word-boundary match, case-insensitive. \b inside Python re works
        # well enough for ASCII; for the German umlaut case we extend the
        # boundary class manually.
        pattern = r"(?<![\wäöüÄÖÜß])" + re.escape(sounds) + r"(?![\wäöüÄÖÜß])"
        out = re.sub(pattern, canon, out, flags=re.IGNORECASE)
    return out


def _cache_key(mode: str, config) -> tuple:
    if mode == "local":
        # Include vocab prompt in the cache key so a Lexikon edit forces a
        # new LocalTranscriber instance (the prompt is baked into the model
        # at construction).
        return (
            mode,
            config.local_model,
            config.local_device,
            _vocab_prompt(config),
        )
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
        # Cloud modes: vocab is applied via inst.initial_prompt after
        # construction, so re-sync from current config on every access
        # (vocab edits don't invalidate the cache key for cloud).
        if hasattr(cached, "initial_prompt") and mode != "local":
            cached.initial_prompt = _vocab_prompt(config)
        return cached
    if mode == "local":
        from .local import LocalTranscriber

        inst = LocalTranscriber(
            model=config.local_model,
            device=config.local_device,
            initial_prompt=_vocab_prompt(config),
        )
    elif mode == "subunit":
        from .subunit import SubunitTranscriber

        inst = SubunitTranscriber(
            endpoint=config.subunit_endpoint,
            api_key=getattr(config, "subunit_api_key", ""),
        )
        inst.initial_prompt = _vocab_prompt(config)
    elif mode == "openai":
        from .cloud import CloudTranscriber, PROVIDER_PRESETS

        inst = CloudTranscriber(
            provider_name="OpenAI",
            endpoint=PROVIDER_PRESETS["openai"]["endpoint"],
            api_key=config.openai_api_key,
            model=config.openai_model or PROVIDER_PRESETS["openai"]["model"],
        )
        inst.initial_prompt = _vocab_prompt(config)
    elif mode == "groq":
        from .cloud import CloudTranscriber, PROVIDER_PRESETS

        inst = CloudTranscriber(
            provider_name="Groq",
            endpoint=PROVIDER_PRESETS["groq"]["endpoint"],
            api_key=config.groq_api_key,
            model=config.groq_model or PROVIDER_PRESETS["groq"]["model"],
        )
        inst.initial_prompt = _vocab_prompt(config)
    elif mode == "custom":
        from .cloud import CloudTranscriber

        inst = CloudTranscriber(
            provider_name="Custom",
            endpoint=config.custom_endpoint,
            api_key=config.custom_api_key,
            model=config.custom_model or "whisper-1",
        )
        inst.initial_prompt = _vocab_prompt(config)
    else:
        raise TranscriberError(f"Unknown mode: {mode}")
    _TRANSCRIBER_CACHE[key] = inst
    return inst


def clear_cache() -> None:
    """Clear all cached transcribers — used after settings changes."""
    _TRANSCRIBER_CACHE.clear()
