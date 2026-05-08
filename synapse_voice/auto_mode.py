"""Auto-mode: pick the best cleanup style based on the active window.

Uses the same window-title we already capture in `target_lock` for the
paste path — no new permissions, no new privacy surface. Pattern-matching
against a curated table; falls back to the user's manual pick if nothing
matches confidently.

Style choices match `synapse_voice.transcriber.cleanup` (server-side):
    "prompt" — structured AI-prompt rewrite
    "email"  — polite email body
    "slack"  — short casual chat message
    "formal" — business / executive tone

Returning None means "keep whatever the user picked manually" — the
caller decides whether to fall back to `config.cleanup_style` or to
disable cleanup entirely for that transcription.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


# Each rule: (style, regex_pattern). The first match wins so order
# matters — put the *most specific* patterns at the top so a generic
# "*chrome*" rule doesn't shadow "ChatGPT - … - Chrome".
#
# Patterns match case-insensitively against the full window title.
@dataclass(frozen=True)
class Rule:
    style: str
    pattern: str
    label: str  # human-readable name shown in the tray toast


_RULES: tuple[Rule, ...] = (
    # ── PROMPT (AI chats + code editors) ─────────────────────────────
    Rule("prompt", r"chat\.openai\.com|ChatGPT", "ChatGPT"),
    Rule("prompt", r"claude\.ai|Claude", "Claude"),
    Rule("prompt", r"gemini\.google\.com|Google Gemini", "Gemini"),
    Rule("prompt", r"perplexity\.ai|Perplexity", "Perplexity"),
    Rule("prompt", r"Cursor", "Cursor"),
    Rule("prompt", r"Visual Studio Code|VS Code|VSCode|VSCodium", "VS Code"),
    Rule("prompt", r"JetBrains|IntelliJ IDEA|PyCharm|WebStorm|GoLand|RubyMine|CLion|Rider|PhpStorm|DataGrip", "JetBrains"),
    Rule("prompt", r"Sublime Text", "Sublime Text"),
    Rule("prompt", r"Zed Editor|^Zed —", "Zed"),
    Rule("prompt", r"Neovim|^nvim|^vim ", "Vim"),
    Rule("prompt", r"\bTerminal\b|iTerm|Konsole|Alacritty|kitty|Hyper", "Terminal"),
    Rule("prompt", r"Windows-Terminal|cmd\.exe|PowerShell|Eingabeaufforderung", "Terminal"),

    # ── EMAIL ────────────────────────────────────────────────────────
    Rule("email", r"Gmail|gmail\.com|mail\.google\.com", "Gmail"),
    Rule("email", r"Outlook|outlook\.live\.com|outlook\.office\.com", "Outlook"),
    Rule("email", r"^Apple Mail|Mail —|^Mail$|Posteingang", "Apple Mail"),
    Rule("email", r"Thunderbird|Mozilla Thunderbird", "Thunderbird"),
    Rule("email", r"^Spark —|Spark Mail", "Spark"),
    Rule("email", r"\bProtonMail\b|mail\.proton\.me", "ProtonMail"),
    Rule("email", r"\bFastmail\b|fastmail\.com", "Fastmail"),
    Rule("email", r"\bHey\b — |hey\.com", "Hey"),

    # ── SLACK / chat / DM ────────────────────────────────────────────
    Rule("slack", r"^Slack \||Slack —|app\.slack\.com", "Slack"),
    Rule("slack", r"\bDiscord\b|discord\.com/channels", "Discord"),
    Rule("slack", r"\bTelegram\b|web\.telegram", "Telegram"),
    Rule("slack", r"\bWhatsApp\b|web\.whatsapp", "WhatsApp"),
    Rule("slack", r"Microsoft Teams|teams\.microsoft\.com", "MS Teams"),
    Rule("slack", r"\bSignal\b — Privater|^Signal$", "Signal"),
    Rule("slack", r"\biMessage\b|^Messages —", "iMessage"),
    Rule("slack", r"\bMattermost\b|\bRocket\.Chat\b", "Chat"),

    # ── FORMAL (long-form documents) ─────────────────────────────────
    Rule("formal", r"Microsoft Word|^Word —|\.docx$", "Word"),
    Rule("formal", r"LibreOffice Writer|OpenOffice Writer", "LibreOffice"),
    Rule("formal", r"^Pages —|\bPages\b — Apple", "Pages"),
    Rule("formal", r"Google Docs|docs\.google\.com/document", "Google Docs"),

    # Notion is intentionally NOT in here — it's used for so many
    # different content types (notes, wikis, tasks, journaling) that
    # picking one style would surprise users. Falls through to default.
)


def detect(window_title: Optional[str]) -> Optional[tuple[str, str]]:
    """Return (style, source_label) for the given window title.

    Returns None if no rule matched — caller should fall back to the
    user's configured `cleanup_style`. Never raises; an empty/None
    title returns None silently.
    """
    if not window_title:
        return None
    for rule in _RULES:
        if re.search(rule.pattern, window_title, re.IGNORECASE):
            return rule.style, rule.label
    return None


def apply_overrides(
    detection: Optional[tuple[str, str]],
    overrides: dict[str, str],
    window_title: Optional[str],
) -> Optional[tuple[str, str]]:
    """Apply user-defined overrides on top of the curated table.

    `overrides` is `{ pattern: style }` — keys are case-insensitive
    substrings to match against the window title (NOT regex, kept simple
    for the Settings UI). User overrides take priority over the curated
    table; if any user pattern matches, that wins.
    """
    if not window_title:
        return detection
    title_low = window_title.lower()
    for pat, style in overrides.items():
        if not pat:
            continue
        if pat.lower() in title_low and style in {"prompt", "email", "slack", "formal", "tidy"}:
            return style, f"custom: {pat[:24]}"
    return detection


# Public for testing — list every rule label so tests + Settings can
# render the curated catalogue without re-importing private state.
def catalog() -> list[tuple[str, str, str]]:
    """Return [(style, label, pattern), ...] for the full curated table."""
    return [(r.style, r.label, r.pattern) for r in _RULES]
