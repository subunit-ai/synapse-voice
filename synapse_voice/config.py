"""Persistent configuration for Sonar."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "synapse-voice"
CONFIG_FILE = CONFIG_DIR / "config.json"


# v0.9.17: Baseline DACH vocabulary shipped from day 1 so Whisper biases
# toward the correct spellings of the brand + recurring tech words. The
# `sounds_like` strings cover Whisper's most common mishears (TJ + Erik
# logs: "Inzin"→"Instant", "Sun-Art"→"Sonar", "transkri**vie**ren"→
# "transkribieren"). User can edit / delete any of these in Settings →
# Vocabulary; the seeding only happens once, gated by
# `vocabulary_default_seeded` so deletions stick.
DEFAULT_VOCABULARY: list[dict] = [
    # Brand / product names
    {"sounds_like": "Sun-Art",   "write_as": "Sonar",
     "aliases": ["Sonnar", "Sonnar.", "Sonar."], "category": "Company"},
    {"sounds_like": "Sub-Unit",  "write_as": "Subunit",
     "aliases": ["Subunit.", "Sub unit", "Subnit", "Subunit AI"], "category": "Company"},
    {"sounds_like": "Synaps",    "write_as": "Synapse",
     "aliases": ["Synaps."], "category": "Company"},
    {"sounds_like": "Es-Enn-I",  "write_as": "SNI",
     "aliases": ["S N I"], "category": "Tech"},
    {"sounds_like": "Higgs Field", "write_as": "Higgsfield",
     "aliases": ["Higs Field", "Higsfield"], "category": "Company"},

    # Tech terms Whisper consistently mangles in German
    {"sounds_like": "Instant",   "write_as": "Instant",
     "aliases": ["Inzin", "Insent", "Instent", "Instand"], "category": "Tech"},
    {"sounds_like": "transkrivieren", "write_as": "transkribieren",
     "aliases": ["transkriebieren", "transkrieben", "transkrivieren"],
     "category": "Tech"},
    {"sounds_like": "transkriviert", "write_as": "transkribiert",
     "aliases": ["transkrieviert", "transkriebiert"], "category": "Tech"},
    {"sounds_like": "Whisper",   "write_as": "Whisper",
     "aliases": ["Wisper", "Visper"], "category": "Tech"},
    {"sounds_like": "Klaud",     "write_as": "Claude",
     "aliases": ["Klod", "Klode"], "category": "Tech"},
    {"sounds_like": "Antropik",  "write_as": "Anthropic",
     "aliases": ["Antrobik"], "category": "Company"},
    {"sounds_like": "Open-AI",   "write_as": "OpenAI",
     "aliases": ["Open A I", "Openei"], "category": "Company"},
    {"sounds_like": "Em-Ce-Pe",  "write_as": "MCP",
     "aliases": ["M C P", "M.C.P."], "category": "Tech"},
    {"sounds_like": "DSGVO",     "write_as": "DSGVO",
     "aliases": ["D S G V O", "D.S.G.V.O."], "category": "Tech"},

    # People recurring in workflow
    {"sounds_like": "Erik",      "write_as": "Erik",
     "aliases": ["Eric"], "category": "Person"},
    {"sounds_like": "Te-Je",     "write_as": "TJ",
     "aliases": ["T J", "T.J.", "Tee Jay"], "category": "Person"},
]


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
    # v0.7.1: pick the overlay's renderer style. Each style reacts to the
    # mic level differently — pick what feels right.
    #   "sphere"  — current default: Verlet-physics glass spheres
    #   "sonar"   — animated Sonar logo (pulsing rings + 5 audio-reactive bars)
    #   "bars"    — vertical equalizer-style bars
    #   "wave"    — horizontal sine waveform
    #   "classic" — minimal cyan dot (Bubble-era throwback)
    orb_overlay_style: str = "sphere"
    # v0.9.16 (TJ-feedback): scale multiplier for the orb overlay so
    # users on hi-DPI tablets can size it up without us hardcoding a
    # second variant. 1.0 = legacy default (~132px window), 1.5/2.0/3.0
    # bumps both the visible dot and its hit-area proportionally.
    orb_overlay_size: float = 1.0  # 0.5 .. 3.0
    # v0.9.17 (TJ): hide the overlay entirely while idle — surface it
    # only while the user is actively recording. Off by default to keep
    # the existing always-visible behaviour for users who rely on the
    # orb as a "the app is alive" indicator.
    orb_overlay_auto_hide: bool = False
    # v0.10.0: emergency-rollback flag — set to True to revive the
    # legacy multi-window MainWindow + SettingsDialog layout in case
    # the new Hub UI hits a Win-ARM regression we can't reproduce
    # locally. Default off so everyone gets the new shell.
    use_legacy_main_window: bool = False

    # v0.8.0 (Codex Top 1): Speaker diarization for long-form recordings.
    # Server-side via transcribe.subunit.ai /v1/diarize — bundles the
    # 600MB torch/diarize stack we don't want in the AppImage. Only runs
    # for recordings >= long_form_threshold_seconds, and only when the
    # user is paired with a Subunit account (uses the same X-API-Key as
    # cleanup). Off by default — opt-in toggle in Settings → Account.
    diarization_enabled: bool = False
    # Optional ceiling for the spectral-clustering speaker count. Most
    # client meetings are 2-6 people; 8 is a sensible default ceiling.
    diarization_max_speakers: int = 8

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

    # v0.6.0/v0.6.1: Long-form mode.  When a recording is at least
    # `long_form_threshold_seconds` long, swap the cleanup style to
    # `long_form_cleanup_style` for that one transcription.
    #
    # v0.6.1 (TJ-feedback msg 2868/2869):
    #   - threshold raised 60s → 240s — 2-3 minute dictations are
    #     normal and shouldn't get auto-summarised
    #   - default style is now "raw" (no cleanup), not "summary" — TJ
    #     wants long recordings as raw transcript so the Subunit App
    #     can later extract summary / action items / calendar / decisions
    #     downstream rather than the cleanup layer destroying the raw
    #     content at capture time
    # Set the threshold to 0 to disable the long-form auto-switch.
    long_form_threshold_seconds: int = 240
    long_form_cleanup_style: str = "raw"  # raw | summary | action_items | minutes | decisions

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

    # v0.9.5 (2026-05-16): Subunit-Account tokens (auth.subunit.ai).
    # Replaces the per-user API-key flow. After a successful browser
    # login via subunit_auth.login_interactive(), these are populated
    # and the cloud transcriber uses them as a Bearer token. Storage
    # is plaintext in the same JSON config that already holds API keys
    # — same trust boundary.
    subunit_access_token: str = ""
    subunit_refresh_token: str = ""
    subunit_token_issued_at: float = 0.0  # epoch seconds
    subunit_token_expires_in: int = 0     # seconds since issued_at
    subunit_workspace_id: str = ""

    # 2026-05-16: Cloud-side Quality vs Fast vs Auto switch (Subunit provider).
    # "auto"    → server picks instant (base) for <5s, fast (small) for <20s, quality otherwise
    # "instant" → base (~12× faster, only good for one-word utterances)
    # "fast"    → small (~4× faster, solid German)
    # "quality" → large-v3-turbo (best accuracy, slower)
    # 2026-05-18: default reverted from "auto" → "quality" — auto's instant/fast
    # tiers degraded German accuracy on Erik's typical dictations. Auto stays
    # opt-in via the UI toggle for users who want speed on short clips.
    cloud_quality_mode: str = "quality"  # "auto" | "instant" | "fast" | "quality"

    # v0.4: Subtle UI sounds — start ping on record, pop on done.
    sound_enabled: bool = True
    sound_volume: float = 0.6  # 0.0..1.0

    # v0.3.9: Lexikon — custom-vocab to bias Whisper toward correct
    # spellings of brand names / technical terms / proper nouns. Each
    # entry: {"sounds_like": "z.B. wie es klingt", "write_as": "Korrekt"}.
    # The "write_as" values feed Whisper as initial_prompt; both are also
    # used in a post-process replace so persistent mishears are corrected.
    #
    # v0.9.12 (Vocabulary v2): each entry may also carry
    #   "category": Person | Company | Tech | Place | Other
    #   "aliases": list[str] of additional sounds_like patterns
    # Old entries without these fields keep working — they're migrated
    # to category="Other"/aliases=[] on first read.
    vocabulary: list[dict] = field(default_factory=list)
    # v0.9.17: marker so the default DACH brand+tech vocabulary is seeded
    # only ONCE — re-running app shouldn't re-add terms the user deleted.
    vocabulary_default_seeded: bool = False

    # v0.9.12: DACH Formatting Pack. Post-process pass that fixes German
    # abbreviation spacing, currency phrases, punctuation spacing and
    # turns ASCII straight quotes into German curly „…". Off by default
    # so existing dictation behavior is unchanged unless opted in.
    dach_format_enabled: bool = False

    history_size: int = 50
    history: list[dict] = field(default_factory=list)
    # v0.9.11: Privacy switch — when off, transcripts are no longer
    # persisted to history (counters still tick so the Settings totals
    # stay honest). Turn off via Settings → Privatsphäre. Existing
    # entries stay until the user clears them manually.
    history_enabled: bool = True
    # Stats
    total_transcriptions: int = 0
    total_audio_seconds: float = 0.0

    @classmethod
    def _seed_default_vocabulary(self) -> None:
        """v0.9.17: append baseline DACH terms to the user's vocabulary
        if we haven't already done so. Idempotent — toggling the flag
        on the existing entries prevents re-seeding after deletion."""
        if getattr(self, "vocabulary_default_seeded", False):
            return
        existing = {(e.get("write_as") or "").strip().lower()
                    for e in (self.vocabulary or []) if isinstance(e, dict)}
        if not isinstance(self.vocabulary, list):
            self.vocabulary = []
        for entry in DEFAULT_VOCABULARY:
            canon = entry.get("write_as", "").strip().lower()
            if canon and canon not in existing:
                # Copy so later edits to one don't mutate the constant.
                self.vocabulary.append(dict(entry))
                existing.add(canon)
        self.vocabulary_default_seeded = True

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_FILE.exists():
            cfg = cls()
            cfg._seed_default_vocabulary()
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
            # v0.9.14: cloud_quality_mode default reverted "auto" → "quality"
            # because Auto's instant/fast tiers degraded German accuracy on
            # short dictations. Anyone who never explicitly toggled (i.e. is
            # carrying the old silent default) gets bumped back to "quality".
            # Users who actually want Auto can re-pick it in the UI; the
            # migration only runs once.
            if not getattr(cfg, "cloud_quality_migrated_v0914", False):
                if cfg.cloud_quality_mode in ("auto", ""):
                    cfg.cloud_quality_mode = "quality"
                cfg.cloud_quality_migrated_v0914 = True
                cfg.save()
            # v0.9.12: backfill new Vocabulary v2 fields on legacy entries
            # so the rest of the app can rely on category/aliases existing.
            #
            # v0.9.13 (Codex P1): guard against malformed entries.
            # Before this guard, a single non-dict item in `vocabulary`
            # (e.g. a stray string left in by a buggy migration or
            # hand-edit) would raise TypeError, fall through to the
            # outer except, and trigger the "corrupted config" rename
            # path — wiping ALL settings + history. Now we silently
            # drop malformed entries with a stderr note.
            migrated = False
            cleaned: list[dict] = []
            dropped = 0
            for entry in cfg.vocabulary or []:
                if not isinstance(entry, dict):
                    dropped += 1
                    continue
                if "category" not in entry:
                    entry["category"] = "Other"
                    migrated = True
                if "aliases" not in entry:
                    entry["aliases"] = []
                    migrated = True
                # Also normalise aliases to a list[str] — strings are
                # iterable and a hand-edited config can easily end up
                # with "aliases": "foo" which would later create one
                # replacement pattern per character.
                aliases = entry.get("aliases")
                if not isinstance(aliases, list):
                    entry["aliases"] = []
                    migrated = True
                else:
                    norm = [a for a in aliases if isinstance(a, str) and a.strip()]
                    if norm != aliases:
                        entry["aliases"] = norm
                        migrated = True
                cleaned.append(entry)
            if dropped:
                import sys as _sys
                print(
                    f"[config] dropped {dropped} malformed vocabulary entries",
                    file=_sys.stderr,
                )
                cfg.vocabulary = cleaned
                migrated = True
            if migrated:
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
        # 2026-05-16 (Codex P2): the config holds refresh_tokens + BYO
        # API keys. Tighten the file mode to 0600 (owner read/write only)
        # so a shared-machine attacker can't grep the home dir for it.
        # POSIX-only — Windows just inherits ACLs from the parent.
        try:
            import os
            if hasattr(os, "chmod"):
                os.chmod(CONFIG_FILE, 0o600)
        except Exception:
            pass
