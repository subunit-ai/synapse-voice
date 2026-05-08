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
    mode: str = "local"  # local | openrouter | subunit
    local_model: str = "base"  # base | small | medium | large-v3
    local_device: str = "auto"  # auto | cpu | cuda
    language: str = "de"
    openrouter_api_key: str = ""
    subunit_endpoint: str = "https://transcribe.subunit.ai/v1/transcribe"
    subunit_api_key: str = ""
    autopaste: bool = True
    target_lock: bool = True
    show_bubble: bool = True
    history_size: int = 50
    history: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            data = json.loads(CONFIG_FILE.read_text())
            valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
            return cls(**valid)
        except (json.JSONDecodeError, TypeError):
            return cls()

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2))
