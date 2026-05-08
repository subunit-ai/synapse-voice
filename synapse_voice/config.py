"""Persistent configuration for Synapse Voice."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "synapse-voice"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    hotkey: str = "<ctrl>+<shift>+<space>"
    # mode: "local" | "subunit" | "openai" | "groq" | "custom"
    mode: str = "local"
    local_model: str = "base"  # base | small | medium | large-v3
    local_device: str = "auto"  # auto | cpu | cuda
    language: str = "de"

    # Subunit DSGVO endpoint (default — our own server)
    subunit_endpoint: str = "https://transcribe.subunit.ai/v1/transcribe"
    subunit_api_key: str = ""

    # OpenAI Whisper (api.openai.com)
    openai_api_key: str = ""
    openai_model: str = "whisper-1"

    # Groq Whisper (free tier, ~10x realtime)
    groq_api_key: str = ""
    groq_model: str = "whisper-large-v3-turbo"

    # Custom OpenAI-compatible endpoint
    custom_endpoint: str = ""
    custom_api_key: str = ""
    custom_model: str = "whisper-1"

    # Legacy field — pre-v0.2.5 used OpenRouter via this key. Kept so old
    # configs migrate quietly; no longer wired to anything functional.
    openrouter_api_key: str = ""

    autopaste: bool = True
    target_lock: bool = True
    show_bubble: bool = True

    # v0.3.0: AI cleanup layer
    cleanup_enabled: bool = False
    cleanup_style: str = "tidy"  # tidy | formal

    # v0.3.0: Recording mode
    recording_mode: str = "toggle"  # toggle | hold

    # v0.3.0: Account (subunit-server side)
    account_email: str = ""

    # v0.3.0: Auto-update
    auto_update_check: bool = True

    history_size: int = 50
    history: list[dict] = field(default_factory=list)
    # Stats
    total_transcriptions: int = 0
    total_audio_seconds: float = 0.0

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            data = json.loads(CONFIG_FILE.read_text())
            valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            cfg = cls(**valid)
            # Migrate pre-v0.2.5 "openrouter" mode → "openai" (the OpenRouter
            # whisper endpoint never actually existed; their /audio/transcriptions
            # returns HTTP 400 for any request). Persist the migration so the
            # legacy mode doesn't keep getting re-translated each launch.
            if cfg.mode == "openrouter":
                cfg.mode = "openai"
                cfg.save()
            return cfg
        except (json.JSONDecodeError, TypeError):
            # Corrupted config — back it up so the user can recover the history,
            # then start fresh. Don't silently overwrite.
            try:
                from datetime import datetime

                ts = datetime.now().strftime("%Y%m%d-%H%M%S")
                backup = CONFIG_DIR / f"config.broken-{ts}.json"
                CONFIG_FILE.rename(backup)
            except Exception:
                pass
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))
