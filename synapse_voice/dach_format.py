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


_CURRENCY_RE = re.compile(
    r"(?<![\wäöüÄÖÜß])"
    r"(?P<num>[a-zäöüß]+|\d+(?:[.,]\d+)?)"
    r"\s+(?P<unit>Euro|Cent|CHF|Franken)"
    r"(?![\wäöüÄÖÜß])",
    flags=re.IGNORECASE,
)


def _format_currency_match(m: re.Match) -> str:
    raw = m.group("num")
    unit = m.group("unit").lower()
    if raw.isdigit() or re.fullmatch(r"\d+[.,]\d+", raw):
        n = raw
    else:
        parsed = _parse_german_compound(raw)
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
