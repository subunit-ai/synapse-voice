"""AI cleanup-layer for transcribed text.

Routes the raw whisper output through Claude Haiku via OpenRouter to:
  - remove filler words (ähm, äh, halt, also)
  - fix punctuation + capitalisation
  - close half-finished sentences
without changing the speaker's actual content.

Configurable via STYLE:
  - "tidy"   (default): light cleanup, keeps wording
  - "formal": rewrite into business-formal tone
  - "prompt": rewrite messy spoken prompt → structured AI prompt
  - "email" : rewrite into a polite, well-structured email
  - "slack" : rewrite into a short, casual chat message
  - "raw"   : passthrough (no cleanup, used for the disabled toggle path)
"""
from __future__ import annotations

import os
from typing import Literal

import httpx

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CLEANUP_MODEL = os.environ.get("CLEANUP_MODEL", "anthropic/claude-haiku-4-5")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

PROMPTS: dict[str, str] = {
    "tidy": (
        "You are a transcription cleanup assistant. The user dictated the text "
        "below to a speech-to-text system. Your job:\n"
        "  - remove filler words (ähm, äh, halt, also, you know)\n"
        "  - fix punctuation + capitalisation\n"
        "  - close half-finished sentences with the speaker's evident intent\n"
        "  - keep the speaker's word choice and tone\n"
        "  - keep the same language as the input\n"
        "Return ONLY the cleaned text. No commentary, no quotes, no preamble.\n"
        "If the text is already clean, return it unchanged."
    ),
    "formal": (
        "You are a transcription cleanup assistant. The user dictated the text "
        "below to a speech-to-text system. Your job:\n"
        "  - rewrite into business-formal tone\n"
        "  - remove filler words and conversational tics\n"
        "  - fix punctuation + capitalisation\n"
        "  - keep the speaker's actual content; do not invent details\n"
        "  - keep the same language as the input\n"
        "Return ONLY the cleaned text. No commentary, no quotes, no preamble."
    ),
    "prompt": (
        "You are a prompt-engineering assistant. The user dictated a rough "
        "request to a speech-to-text system, intending to send it to an AI "
        "(Claude / GPT / coding agent). Rewrite the dictation into a clean, "
        "well-structured prompt:\n"
        "  - keep the speaker's intent + every concrete detail (names, paths, "
        "    versions, constraints, edge cases)\n"
        "  - drop filler, false starts, self-corrections — keep the corrected "
        "    version\n"
        "  - structure: 1-line goal at the top, then bullet points for "
        "    constraints / requirements / context if there is more than one\n"
        "  - if the speaker mentions code, file paths, or commands, format "
        "    them with backticks\n"
        "  - if the speaker is asking for code: end with a 1-line success "
        "    criterion (\"so that …\" or \"such that …\")\n"
        "  - keep the speaker's language (German prompts stay German)\n"
        "  - DO NOT add information the speaker did not provide\n"
        "  - DO NOT answer the prompt — only rewrite it\n"
        "Return ONLY the rewritten prompt. No commentary, no preamble, "
        "no quotation marks around it."
    ),
    "email": (
        "You are a transcription cleanup assistant. The user dictated an "
        "email body to a speech-to-text system. Your job:\n"
        "  - structure into a clear opener / body / closer\n"
        "  - polite, professional tone, but not stiff\n"
        "  - remove fillers + spoken-only artefacts\n"
        "  - keep every concrete detail; do not invent any\n"
        "  - keep the speaker's language\n"
        "Return ONLY the cleaned email body. No subject line, no commentary."
    ),
    "slack": (
        "You are a transcription cleanup assistant. The user dictated a "
        "short chat message (Slack / Telegram / Teams) to a speech-to-text "
        "system. Your job:\n"
        "  - keep it short, casual, direct — like a real chat\n"
        "  - one or two sentences, no greeting + sign-off\n"
        "  - drop fillers + repeats\n"
        "  - emoji are OK if the speaker mentioned one\n"
        "  - keep the speaker's language + tone\n"
        "Return ONLY the cleaned message. No commentary, no quotes."
    ),
}

Style = Literal["tidy", "formal", "prompt", "email", "slack", "raw"]


class CleanupError(RuntimeError):
    pass


async def cleanup(text: str, style: Style = "tidy") -> str:
    if style == "raw" or not text.strip():
        return text
    prompt = PROMPTS.get(style)
    if prompt is None:
        raise CleanupError(f"unknown cleanup style: {style}")
    if not OPENROUTER_KEY:
        raise CleanupError(
            "OPENROUTER_API_KEY not configured on server — cleanup unavailable"
        )

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            r = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": CLEANUP_MODEL,
                    "messages": [
                        {"role": "system", "content": prompt},
                        {"role": "user", "content": text},
                    ],
                    "max_tokens": min(2048, max(64, int(len(text) * 1.5))),
                    "temperature": 0.1,
                },
            )
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:200] if e.response is not None else ""
            raise CleanupError(f"OpenRouter HTTP {e.response.status_code}: {body}") from e
        except httpx.HTTPError as e:
            raise CleanupError(f"OpenRouter request failed: {e}") from e
        try:
            data = r.json()
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, ValueError, IndexError) as e:
            raise CleanupError(f"OpenRouter returned unexpected payload: {r.text[:200]}") from e
