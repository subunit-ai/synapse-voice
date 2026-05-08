"""Client-side AI cleanup — calls the subunit-server /v1/cleanup endpoint.

Used when Config.cleanup_enabled is True. The server routes the request
through Claude Haiku via OpenRouter to remove filler words, fix
punctuation, and close half-finished sentences.

If the server is unreachable or returns an error, the original text is
returned unchanged (cleanup is best-effort, never blocks the paste).
"""
from __future__ import annotations

import requests

from .logger import get as _get_logger

_log = _get_logger(__name__)


def _cleanup_url(transcribe_endpoint: str) -> str:
    base = transcribe_endpoint.rstrip("/")
    for marker in ("/v1/transcribe", "/v1"):
        if base.endswith(marker):
            base = base[: -len(marker)]
            break
    return f"{base.rstrip('/')}/v1/cleanup"


def cleanup_text(
    text: str,
    transcribe_endpoint: str,
    api_key: str,
    style: str = "tidy",
    timeout: float = 15.0,
) -> str:
    """Best-effort cleanup. Returns the original text unchanged on any error."""
    if not text or style == "raw":
        return text
    url = _cleanup_url(transcribe_endpoint)
    headers: dict[str, str] = {}
    if api_key:
        headers["X-API-Key"] = api_key
    try:
        r = requests.post(
            url,
            headers=headers,
            json={"text": text, "style": style},
            timeout=timeout,
        )
        r.raise_for_status()
        cleaned = (r.json().get("text") or "").strip()
        if not cleaned:
            return text
        return cleaned
    except requests.HTTPError as e:
        body = ""
        status = "?"
        try:
            status = str(e.response.status_code)
            body = e.response.text[:200]
        except Exception:
            pass
        _log.warning("Cleanup HTTP %s: %s — using raw text", status, body)
        return text
    except requests.RequestException as e:
        _log.warning("Cleanup request failed: %s — using raw text", e)
        return text
