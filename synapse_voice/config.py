"""Persistent configuration for Sonar."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "synapse-voice"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    hotkey: str = "<ctrl>+<space>"
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

    # v0.5: Mic-device picker. Empty string = system default. Otherwise
    # the device name (we resolve to the index at startup so a hardware
    # change doesn't pin to a stale index).
    mic_device_name: str = ""

    # v0.4: Orb overlay (Voicely-style floating glass spheres). The new
    # default visual feedback layer — replaces the simple Bubble. Disable
    # via Settings if you want the minimal old-style notifier back.
    use_orb_overlay: bool = True
    orb_color_theme: str = "cyan"  # cyan | violet | mint
    # Position: one of the named anchors (bottom-center, bottom-left,
    # bottom-right, top-center, top-left, top-right) or "custom-X-Y"
    # where X/Y are screen-relative pixel offsets (set by user drag).
    # Default = bottom-center (TJ-confirmed: "soll mittig platziert sein").
    orb_position: str = "bottom-center"
    orb_idle_pulse: bool = True

    # v0.3.0: AI cleanup layer (default off — TJ-pref)
    cleanup_enabled: bool = False
    # v0.3.24: default style flipped from tidy → prompt because Sonar's
    # primary use is dictating prompts to AI agents. Tidy was dropped from
    # the picker (TJ feedback "kommt mir komisch"). Old configs with
    # cleanup_style='tidy' get migrated to 'prompt' on load.
    cleanup_style: str = "prompt"  # prompt | email | slack | formal

    # v0.3.25: Auto-Mode — pick cleanup_style automatically based on the
    # active window. Off by default so existing user choices stick; the
    # Onboarding wizard advertises this and flips it on if the user opts
    # in. `auto_mode_overrides` is a user-editable dict of substring →
    # style; matches override the curated table in synapse_voice.auto_mode.
    cleanup_auto_mode: bool = False
    auto_mode_overrides: dict = field(default_factory=dict)

    # v0.6.0: Long-form mode (read.ai-inspired).  When a recording is
    # at least `long_form_threshold_seconds` long, override the user's
    # configured cleanup_style with `long_form_cleanup_style` for that
    # one transcription.  Lets a single hotkey serve both short dictation
    # ("send a Slack message") and long captures ("here's the meeting
    # I just had").  Set the threshold to 0 to disable.
    long_form_threshold_seconds: int = 60
    long_form_cleanup_style: str = "summary"  # summary | action_items | minutes | decisions

    # v0.3.29: Subunit Suite — Voice → Synapse Knowledge Base bridge.
    # When on, every transcript is POSTed to /v1/synapse/save after
    # cleanup, so it shows up in your Synapse semantic-search index.
    # Off by default; opt-in via Settings → Account.
    synapse_save_enabled: bool = False

    # v0.3.0: Recording mode. Default = hold (Push-to-Talk) since TJ
    # confirmed Voicely's default works better — press-and-hold maps
    # naturally to "I'm dictating right now".
    recording_mode: str = "hold"  # toggle | hold

    # v0.3.0: Account (subunit-server side)
    account_email: str = ""

    # v0.3.2: Remember which cloud provider the user last picked, so when
    # they toggle "Process locally" off they go back to the same one.
    last_cloud_mode: str = "subunit"

    # v0.3.0: Auto-update
    auto_update_check: bool = True

    # v0.4: First-launch onboarding wizard. Flips to True after the user
    # finishes (or skips) the 4-step setup.
    has_seen_onboarding: bool = False

    # v0.4: UI language for chrome strings (Onboarding, Settings, Main).
    # "de" or "en". Default = de since most Subunit users are German.
    # Doesn't affect transcription language — that's `language` above.
    ui_language: str = "de"

    # v0.3.21: UI theme — applies a Qt palette across the whole app. Dark
    # is the brand default (matches Marketing site + Voicely-style chrome);
    # Light is for users who prefer a bright IDE look.
    ui_theme: str = "dark"  # dark | light

    # v0.3.21: Plan + trial state — populated when the user signs in via
    # Onboarding. The server is the source of truth (we re-fetch via
    # /v1/account/info on launch); these are cached locally so we can
    # render the badge without a round-trip.
    plan: str = "free"  # free | trial | pro
    trial_started_at: int = 0  # unix seconds, 0 = never started

    # v0.4: Subtle UI sounds — start ping on record, pop on done.
    sound_enabled: bool = True
    sound_volume: float = 0.6  # 0.0..1.0

    # v0.3.9: Lexikon — custom-vocab to bias Whisper toward correct
    # spellings of brand names / technical terms / proper nouns. Each
    # entry: {"sounds_like": "z.B. wie es klingt", "write_as": "Korrekt"}.
    # The "write_as" values feed Whisper as initial_prompt; both are also
    # used in a post-process replace so persistent mishears are corrected.
    vocabulary: list[dict] = field(default_factory=list)

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
            # v0.3.24: tidy was the old default + has been dropped from the
            # picker. Anyone whose saved style is still "tidy" gets bumped
            # to the new default ("prompt") on load.
            if cfg.cleanup_style == "tidy":
                cfg.cleanup_style = "prompt"
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
