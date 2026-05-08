"""AI cleanup-layer for transcribed text.

Routes the raw whisper output through Claude Haiku via OpenRouter to:
  - remove filler words (ähm, äh, halt, also)
  - fix punctuation + capitalisation
  - close half-finished sentences
without changing the speaker's actual content.

Configurable via STYLE:
  - "tidy"   (default): light cleanup, keeps wording
  - "formal": rewrite into business-formal tone
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
}

Style = Literal["tidy", "formal", "raw"]


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
