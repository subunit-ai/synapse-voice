"""DACH Formatting Pack (v0.9.12).

Post-process layer for German/Austrian/Swiss transcripts that fixes the
small mechanical things Whisper consistently gets wrong:

  - Common abbreviations get the correct spacing: ``z.B.`` → ``z. B.``,
    ``d.h.`` → ``d. h.``, etc.
  - Pre/post-punctuation spacing: ``Hallo  ,  Welt`` → ``Hallo, Welt``.
  - Curly German quotation marks: ``"Hallo"`` → ``„Hallo"``.
  - Currency normalisation: ``zweihundert Euro`` → ``200 €``,
    ``20 Euro`` → ``20 €`` (only when an explicit number precedes "Euro"
    so we don't mangle running prose like "der Euro fällt").
  - Common contractions: ``ne`` → ``eine`` is NOT applied — too aggressive.

Conservative by default: anything ambiguous is left alone. The user opts
in via Settings → Sprache / Formatierung.
"""
from __future__ import annotations

import re

# Cardinal numbers spoken in German up to 1 million. Used only inside
# narrow contexts (preceding "Euro"/"Cent") so we don't accidentally
# rewrite numerals that the user said deliberately as words.
_NUM_TENS = {
    "null": 0, "eins": 1, "ein": 1, "eine": 1, "einen": 1,
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11,
    "zwölf": 12, "dreizehn": 13, "vierzehn": 14, "fünfzehn": 15,
    "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19,
    "zwanzig": 20, "dreißig": 30, "vierzig": 40, "fünfzig": 50,
    "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90,
}
_NUM_HUNDRED = "hundert"
_NUM_THOUSAND = "tausend"


def _parse_german_compound(word: str) -> int | None:
    """Best-effort: parse a single German number word like
    ``zweihundertfünfzig`` into ``250``. Returns None if unparseable —
    we never guess, the calling code keeps the original text on None."""
    w = word.lower().strip()
    if not w:
        return None
    if w in _NUM_TENS:
        return _NUM_TENS[w]
    total = 0
    # ``...tausend...`` split
    if _NUM_THOUSAND in w:
        left, _, right = w.partition(_NUM_THOUSAND)
        left_n = _parse_german_compound(left) if left else 1
        right_n = _parse_german_compound(right) if right else 0
        if left_n is None or right_n is None:
            return None
        return left_n * 1000 + right_n
    # ``...hundert...`` split
    if _NUM_HUNDRED in w:
        left, _, right = w.partition(_NUM_HUNDRED)
        left_n = _parse_german_compound(left) if left else 1
        right_n = _parse_german_compound(right) if right else 0
        if left_n is None or right_n is None:
            return None
        return left_n * 100 + right_n
    # ``einundzwanzig``-style: <ones>und<tens>
    m = re.match(r"^([a-zäöüß]+)und([a-zäöüß]+)$", w)
    if m:
        ones = _NUM_TENS.get(m.group(1))
        tens = _NUM_TENS.get(m.group(2))
        if ones is not None and tens is not None and tens % 10 == 0:
            return tens + ones
    return None


# v0.9.13 (Codex P1): match up to 4 space-separated number words so
# "drei tausend Euro" → "3000 €" instead of "drei 1000 €". The previous
# single-token regex picked up just "tausend" as the number, which is
# materially wrong. We allow either one digit-literal or 1..4 number
# words; _format_currency_match then collapses the word sequence by
# concatenating into one compound the parser can handle.
_CURRENCY_RE = re.compile(
    r"(?<![\wäöüÄÖÜß])"
    r"(?P<num>\d+(?:[.,]\d+)?|(?:[a-zäöüß]+(?:\s+[a-zäöüß]+){0,3}))"
    r"\s+(?P<unit>Euro|Cent|CHF|Franken)"
    r"(?![\wäöüÄÖÜß])",
    flags=re.IGNORECASE,
)

# Scale-only words that must NEVER stand alone as "the number" — they
# only mean something when joined to a leading multiplier. Used to
# reject single-token matches like "tausend Euro" (which would otherwise
# rewrite to "1000 €" even though the speaker said something like
# "Aktien für tausend Euro" where "tausend" is part of a phrase).
_SCALE_ONLY = frozenset({"hundert", "tausend", "million", "millionen", "milliarde", "milliarden"})


def _format_currency_match(m: re.Match) -> str:
    raw = m.group("num").strip()
    unit = m.group("unit").lower()
    if re.fullmatch(r"\d+(?:[.,]\d+)?", raw):
        n = raw
    else:
        words = raw.split()
        # Reject single bare scale-words — too ambiguous.
        if len(words) == 1 and words[0].lower() in _SCALE_ONLY:
            return m.group(0)
        # Try the whole phrase as one German compound first
        # ("dreitausend") — that's the common written form. If that
        # fails, try multiplying the first word(s) into the scale
        # word(s) — "drei tausend" → "drei" × "tausend" = 3000.
        parsed = _parse_german_compound("".join(words))
        if parsed is None and len(words) >= 2:
            parsed = _multiply_phrase(words)
        if parsed is None:
            return m.group(0)
        n = str(parsed)
    suffix = {
        "euro": " €",
        "cent": " ct",
        "chf": " CHF",
        "franken": " CHF",
    }.get(unit, f" {m.group('unit')}")
    return f"{n}{suffix}"


def _multiply_phrase(words: list[str]) -> int | None:
    """Fold a phrase like ['drei', 'tausend'] into 3000.

    Walks left-to-right, treating each token as either a numeric piece
    or a scale-word. Returns None on the slightest ambiguity — the
    caller falls back to leaving the original text alone."""
    total = 0
    pending = 0
    has_pending = False
    for w in words:
        wl = w.lower()
        if wl == "und":
            continue
        if wl in ("hundert",):
            mul = pending if has_pending else 1
            pending = mul * 100
            has_pending = True
            continue
        if wl in ("tausend",):
            mul = pending if has_pending else 1
            total += mul * 1000
            pending = 0
            has_pending = False
            continue
        n = _parse_german_compound(w)
        if n is None:
            return None
        if has_pending:
            pending += n
        else:
            pending = n
            has_pending = True
    return total + (pending if has_pending else 0) or None


def _fix_abbreviations(text: str) -> str:
    """Insert the correct narrow no-break-style spacing into common
    abbreviations. We use a regular space rather than U+202F because
    rendering is inconsistent across editors."""
    pairs = [
        (r"\bz\.\s*B\.", "z. B."),
        (r"\bd\.\s*h\.", "d. h."),
        (r"\bu\.\s*a\.", "u. a."),
        (r"\bs\.\s*o\.", "s. o."),
        (r"\bs\.\s*u\.", "s. u."),
        (r"\bbzgl\.", "bzgl."),
        (r"\bggf\.", "ggf."),
        (r"\bca\.", "ca."),
        (r"\busw\.", "usw."),
        (r"\bevtl\.", "evtl."),
    ]
    for pat, repl in pairs:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)
    return text


def _fix_punct_spacing(text: str) -> str:
    """No-space-before-punctuation, single-space-after."""
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,;:!?])(?=[^\s\d])", r"\1 ", text)
    text = re.sub(r" {2,}", " ", text)
    return text


_QUOTE_RE = re.compile(r'"([^"\n]+?)"')


def _fix_quotes(text: str) -> str:
    """ASCII straight double quotes → German curly „…"."""
    return _QUOTE_RE.sub(lambda m: f"„{m.group(1)}“", text)


def format_dach(text: str) -> str:
    """Apply the full DACH formatting pipeline.

    Order matters: currency rewriting first (it expects unmangled
    abbreviations and number words), then abbreviation fix-up, then
    punctuation, finally quotes. All steps are conservative — if a
    pattern doesn't match cleanly we leave the original alone."""
    if not text:
        return text
    out = text
    out = _CURRENCY_RE.sub(_format_currency_match, out)
    out = _fix_abbreviations(out)
    out = _fix_punct_spacing(out)
    out = _fix_quotes(out)
    return out.strip()
