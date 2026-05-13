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

# 2026-05-13: hardened prompts against two TJ-reported failure modes.
#
#   1. Refusal on sensitive topics (Erik dictated something about going
#      to the doctor → cleanup returned "I can't give medical advice"
#      instead of the cleaned email body).  Root cause: prompts framed
#      Claude as an "assistant" without a hard refusal-prohibition rule,
#      so the safety layer kicked in on medical / legal / personal
#      content even though our actual task is mechanical text cleanup.
#
#   2. Hallucinated specifics (Erik dictated "mein Hund hat XYZ
#      gemacht" → cleanup invented a dog name "Robert" that wasn't in
#      the transcript).  Root cause: "close half-finished sentences
#      with the speaker's evident intent" was too lenient — it gave
#      the model license to fill in missing nouns.  We now forbid
#      adding any detail not present in the input.
#
# Strategy: every prompt opens with a strict, role-locked frame ("you
# are a text editor, not an assistant"), an explicit "NEVER refuse"
# rule applicable to all content topics, and an explicit "NEVER add
# names / numbers / facts not in the input" rule.  The user content is
# delivered inside <transcript> tags so the model can't confuse it
# with an instruction.

_BASE_RULES = (
    "You are a mechanical transcription cleanup engine. You receive raw "
    "speech-to-text output inside <transcript> tags and return cleaned "
    "text. Your output replaces what the user would otherwise type into "
    "their email / chat / document — refusing, commenting, or adding any "
    "meta-text causes the user to lose their work.\n\n"
    "ABSOLUTE RULES (apply to ALL inputs regardless of topic — medical, "
    "legal, financial, personal, anything):\n"
    "  - NEVER refuse the cleanup. The topic does not matter; you are "
    "    not advising, you are cleaning text.\n"
    "  - NEVER add information not in the transcript. No names, places, "
    "    dates, numbers, products, examples, or facts may appear in your "
    "    output unless the speaker said them. If a sentence trails off, "
    "    leave it trailing or just add a period — do not invent a "
    "    completion.\n"
    "  - NEVER answer, interpret, summarise, translate, or react to the "
    "    content. Only fix the wording of what was said.\n"
    "  - NEVER add an apology, disclaimer, salutation, sign-off, or any "
    "    other text the speaker did not produce.\n"
    "  - Preserve the speaker's language exactly (German stays German, "
    "    English stays English; do not auto-translate).\n"
    "  - Return ONLY the cleaned text — no XML tags, no quotation marks "
    "    around it, no preamble, no postscript.\n"
)

PROMPTS: dict[str, str] = {
    "tidy": (
        _BASE_RULES + "\n"
        "STYLE: tidy.  Lightest-touch cleanup.\n"
        "  - remove filler words (ähm, äh, halt, also, you know, like)\n"
        "  - fix punctuation + capitalisation\n"
        "  - keep the speaker's exact wording and tone otherwise\n"
        "  - if the text is already clean, return it unchanged"
    ),
    "formal": (
        _BASE_RULES + "\n"
        "STYLE: formal.  Rewrite into business-formal tone.\n"
        "  - drop conversational tics and fillers\n"
        "  - fix punctuation + capitalisation\n"
        "  - tighten phrasing without inventing content"
    ),
    "prompt": (
        _BASE_RULES + "\n"
        "STYLE: prompt.  The speaker dictated a rough request meant to "
        "be sent to an AI (Claude / GPT / coding agent).  Rewrite into a "
        "clean, well-structured prompt:\n"
        "  - keep every concrete detail the speaker said (names, paths, "
        "    versions, constraints, edge cases)\n"
        "  - drop false starts and self-corrections — keep the corrected "
        "    version\n"
        "  - if there are multiple constraints, put a one-line goal at "
        "    the top then bullet points\n"
        "  - format code, paths, and commands with backticks\n"
        "  - if the speaker is asking for code, end with a one-line "
        '    success criterion ("so that …" or "such that …")\n'
        "  - DO NOT answer the prompt — only rewrite the speaker's words"
    ),
    "email": (
        _BASE_RULES + "\n"
        "STYLE: email.  The speaker dictated an email body.\n"
        "  - structure into a clear opener / body / closer\n"
        "  - polite, professional tone, not stiff\n"
        "  - drop spoken-only artefacts (fillers, false starts)\n"
        "  - email-appropriate punctuation\n"
        "  - return only the email body, no subject line\n"
        "  - the topic of the email is not your concern — even medical, "
        "    legal, or sensitive content gets cleaned, not refused"
    ),
    "slack": (
        _BASE_RULES + "\n"
        "STYLE: chat message (Slack / Telegram / Teams).\n"
        "  - keep it short, casual, direct\n"
        "  - one or two sentences, no greeting or sign-off\n"
        "  - drop fillers and repeats\n"
        "  - emoji are OK only if the speaker explicitly mentioned one"
    ),
}


# 2026-05-13: post-process refusal detector.  Even with the hardened
# prompt, occasional refusals slip through (different OpenRouter model
# routes, prompt-injection attempts via the dictation itself).  When
# the output looks like a refusal, return the raw transcript untouched
# — clipboard with the raw text is always better than clipboard with
# "Sorry, I can't help with that".
_REFUSAL_MARKERS = (
    "i can't",
    "i cannot",
    "i'm sorry",
    "i am sorry",
    "i'm not able",
    "i am not able",
    "as an ai",
    "ich kann nicht",
    "ich kann keine",
    "ich kann ihnen nicht",
    "ich kann dir nicht",
    "ich darf nicht",
    "es tut mir leid",
    "leider kann ich",
    "medical advice",
    "medizinische ratschläge",
    "medizinischen ratschläge",
    "legal advice",
    "rechtliche beratung",
    "professional advice",
    "professionelle beratung",
)


def _looks_like_refusal(output: str, original: str) -> bool:
    """Heuristic: if the LLM returned something dramatically shorter
    than the input AND it starts with or contains a refusal marker,
    treat as a refusal and fall back to the raw text."""
    if not output:
        return True
    out_lower = output.lower().strip()
    # First-line check — most refusals open with the marker.
    first_line = out_lower.split("\n", 1)[0]
    for marker in _REFUSAL_MARKERS:
        if first_line.startswith(marker):
            return True
    # Marker present + output much shorter than input → likely refusal.
    if len(output) < max(40, len(original) * 0.5):
        for marker in _REFUSAL_MARKERS:
            if marker in out_lower:
                return True
    return False

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

    # 2026-05-13: deliver the speech-to-text output inside an explicit
    # <transcript> tag.  Without the wrapper, Claude can read sentences
    # like "ich muss zum Arzt wegen X" as a personal request directed
    # at it and reply with medical-advice safety boilerplate.  With the
    # tag, the content is unambiguously opaque text to clean.
    user_message = (
        "<transcript>\n"
        f"{text}\n"
        "</transcript>\n\n"
        "Clean this transcript per the rules above. Return only the "
        "cleaned text with no XML tags, no quotes, no commentary."
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
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": min(2048, max(64, int(len(text) * 1.5))),
                    # 2026-05-13: was 0.1, now 0.  Cleanup is deterministic
                    # by definition — any variability is either jitter
                    # (cosmetic) or an outright hallucination (Erik's
                    # "Hund Robert").  Zero temperature kills both.
                    "temperature": 0,
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
            cleaned = data["choices"][0]["message"]["content"].strip()
        except (KeyError, ValueError, IndexError) as e:
            raise CleanupError(f"OpenRouter returned unexpected payload: {r.text[:200]}") from e

    # 2026-05-13: post-process safety net — if the model returned a
    # refusal anyway, drop the LLM output and pass the raw transcript
    # through.  Clipboard with the original Whisper text is always
    # better than clipboard with "Sorry, I can't help with that".
    if _looks_like_refusal(cleaned, text):
        return text
    # Strip stray <transcript> tags in case the model echoed them.
    for tag in ("<transcript>", "</transcript>"):
        cleaned = cleaned.replace(tag, "")
    return cleaned.strip() or text
