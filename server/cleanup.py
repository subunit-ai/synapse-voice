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
    # 2026-05-13 (read.ai-inspired): meeting + long-form styles.  These
    # are typically applied to multi-minute recordings rather than
    # 30-second dictations.  They share the absolute-rules base so
    # they still refuse to refuse and never invent specifics.
    "summary": (
        _BASE_RULES + "\n"
        "STYLE: meeting summary.  The transcript is a longer recording "
        "(call, meeting, monologue, or extended dictation).\n"
        "  - condense into a structured summary the speaker would write "
        "    themselves to remember what was said\n"
        "  - structure: 1-2 line context / opener, then bullet points "
        "    for the main topics, then a short ‘was beschlossen / what "
        "    was decided’ section if applicable\n"
        "  - cover EVERY substantive point — coverage matters more than "
        "    elegance.  If a topic was discussed, it must appear.\n"
        "  - DO NOT add interpretation, advice, or implications that "
        "    were not voiced.  The summary mirrors the meeting, it does "
        "    not analyse it.\n"
        "  - use the speaker's own terminology and proper nouns "
        "    verbatim (names, products, project codenames)\n"
        "  - if the transcript is too short to be a meeting (under "
        "    ~30 seconds of content), just clean it like the 'tidy' "
        "    style does and skip the structure"
    ),
    "action_items": (
        _BASE_RULES + "\n"
        "STYLE: action-item extraction.\n"
        "Return ONLY action items from the transcript, as a bullet list.\n"
        "  - format each as: '- [@owner] description (due: deadline?)' "
        "    where [@owner] uses the name the speaker mentioned (or "
        "    [@me] if it's a self-task, or leave blank if no owner is "
        "    given) and (due: …) only if a date was mentioned\n"
        "  - include both explicit (‘we need to do X’) and implicit "
        "    (‘ich schreibe Erik wegen Y’) action items\n"
        "  - DO NOT add action items that weren't mentioned\n"
        "  - if there are no action items in the transcript, return "
        "    exactly: 'No action items.'\n"
        "  - one bullet per item, no headers, no preamble"
    ),
    "minutes": (
        _BASE_RULES + "\n"
        "STYLE: formal meeting minutes / Protokoll.\n"
        "Produce a meeting protocol the speaker can paste into a docs "
        "system as the official record of the meeting.\n"
        "  - structure: \n"
        "      Topic / Theme: <1 line>\n"
        "      Participants: <list of names mentioned, or 'not stated'>\n"
        "      Discussion:\n"
        "      - bullet for each topic discussed\n"
        "      Decisions:\n"
        "      - bullet for each decision made (or 'none')\n"
        "      Action Items:\n"
        "      - bullets as in the action_items style (or 'none')\n"
        "  - formal but readable tone\n"
        "  - keep the original language of the meeting\n"
        "  - if a section has nothing, write 'none' rather than omitting"
    ),
    "decisions": (
        _BASE_RULES + "\n"
        "STYLE: decisions extraction.\n"
        "Return ONLY the decisions made in the transcript, as a bullet "
        "list of short, declarative sentences.\n"
        "  - one decision per bullet\n"
        "  - include who decided it if the speaker said so ('TJ: …' or "
        "    'Erik: …'), otherwise omit attribution\n"
        "  - exclude open questions, considerations, and items that "
        "    were discussed but not decided\n"
        "  - if there are no clear decisions, return exactly: "
        "    'No decisions made.'\n"
        "  - no headers, no preamble"
    ),
    # 2026-05-14 (codex review): agency-killer feature — generate a
    # client-ready follow-up email from a meeting transcript.  This is
    # the output agencies actually need every day; bullet-lists of
    # actions are intermediate artefacts.
    "recap_email": (
        _BASE_RULES + "\n"
        "STYLE: client recap email.\n"
        "The transcript is a client call (meeting, status update, "
        "discovery, kickoff). Produce a ready-to-send follow-up email "
        "the speaker would send to the client after the call.\n"
        "Structure:\n"
        "  - Greeting line (use the client name if the speaker said it, "
        "    otherwise 'Hi team,' / 'Hallo zusammen,' matching the "
        "    transcript's language)\n"
        "  - 1-2 sentence thank-you / context opener referring to what "
        "    the call was about\n"
        "  - 'Was wir besprochen haben' / 'What we discussed' — short "
        "    bullets covering the substantive topics\n"
        "  - 'Entscheidungen' / 'Decisions' — bullets, only if any\n"
        "  - 'Naechste Schritte' / 'Next steps' — bullets with owner "
        "    and date when the transcript mentioned them, e.g. "
        "    'TJ schickt das Angebot bis Freitag'\n"
        "  - Friendly close ('Beste Gruesse' / 'Best,') with the "
        "    speaker's name only if they introduced themselves\n"
        "STRICT:\n"
        "  - DO NOT invent topics, decisions, or next steps that were "
        "    not in the transcript. Omit sections that have no content.\n"
        "  - Keep it concise — clients hate long emails.\n"
        "  - Match the transcript's language (German stays German, "
        "    English stays English).\n"
        "  - Return only the email body (no subject line, no preamble)."
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

Style = Literal[
    "tidy", "formal", "prompt", "email", "slack",
    "summary", "action_items", "minutes", "decisions", "recap_email",
    "raw",
]


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
