"""Meeting Check-In endpoints — foundation for meet.subunit.ai.

In-memory session store with random 6-digit codes. The WebRTC streaming
+ per-stream recording layer comes in Phase 2; this MVP is just the
session-management API so the desktop Sonar can issue codes and the
PWA at meet.subunit.ai can resolve / join them.

Endpoints:
  POST /v1/meetings                     → host creates a meeting, gets code + token
  GET  /v1/meetings/<code>/info         → public lookup for the PWA landing
  POST /v1/meetings/<code>/join         → guest joins (name + email, no auth)
  GET  /v1/meetings/<code>/participants → host polls who's joined (host-only)
  POST /v1/meetings/<code>/start        → host marks meeting as recording (no-op in MVP)
  POST /v1/meetings/<code>/end          → host ends the meeting

The "host token" returned at creation time is required for all host-only
actions. The "join token" returned to participants on join is for their
post-meeting recap-email link.
"""
from __future__ import annotations

import json
import os
import random
import secrets
import string
import threading
import time
from typing import Optional


# Lock-protected in-memory store. Production will move to Postgres
# alongside auth.subunit.ai; for the MVP we accept that meetings vanish
# on server restart.
_LOCK = threading.RLock()
_MEETINGS: dict[str, dict] = {}  # code → meeting dict


def _gen_code() -> str:
    """Generate a 6-digit code that doesn't collide with active meetings."""
    for _ in range(50):
        digits = "".join(random.choices(string.digits, k=6))
        with _LOCK:
            if digits not in _MEETINGS:
                return digits
    raise RuntimeError("could not generate unique meeting code")


def _gen_token(length: int = 32) -> str:
    return secrets.token_urlsafe(length)


def _now() -> int:
    return int(time.time())


def _format_created(ts: int) -> str:
    """ISO-8601 in UTC for the wire; local rendering happens client-side."""
    import datetime
    return datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc).isoformat()


def create_meeting(
    *,
    host_name: str,
    host_email: str | None,
    title: str | None,
    api_key: str | None = None,
) -> dict:
    """Allocate a meeting. Returns code + host_token + share_url."""
    code = _gen_code()
    host_token = _gen_token(48)
    meeting = {
        "code": code,
        "title": (title or "").strip() or f"Meeting #{code}",
        "host_name": host_name.strip() or "Host",
        "host_email": (host_email or "").strip() or None,
        "host_token": host_token,
        "host_api_key": api_key,  # bound to caller, for revenue tracking
        "created_at": _now(),
        "status": "open",  # open | recording | ended
        "participants": [],  # list of {token, name, email, joined_at, source}
    }
    with _LOCK:
        _MEETINGS[code] = meeting
    return _public_view_for_host(meeting)


def get_meeting_info(code: str) -> Optional[dict]:
    """Public lookup for the PWA landing page. No auth required."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return None
        return {
            "code": code,
            "title": m["title"],
            "host_name": m["host_name"],
            "created_at": _format_created(m["created_at"]),
            "created_at_local": _human_relative(m["created_at"]),
            "status": m["status"],
            "participant_count": len(m["participants"]),
        }


def join_meeting(code: str, *, name: str, email: str, source: str = "web") -> Optional[dict]:
    """Guest join. Returns the participant record + a personal join_token."""
    if not name.strip() or not email.strip() or "@" not in email:
        return None
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return None
        if m["status"] == "ended":
            return None
        token = _gen_token(24)
        participant = {
            "token": token,
            "name": name.strip()[:80],
            "email": email.strip().lower()[:200],
            "joined_at": _now(),
            "source": source,  # web | qr | code | host
        }
        m["participants"].append(participant)
        return {
            "join_token": token,
            "name": participant["name"],
            "meeting_title": m["title"],
            "host_name": m["host_name"],
            "code": code,
        }


def list_participants(code: str, host_token: str) -> Optional[list[dict]]:
    """Host polls for the live participant list."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return None
        return [
            {
                "name": p["name"],
                "joined_at": _format_created(p["joined_at"]),
                "joined_at_relative": _human_relative(p["joined_at"]),
                "source": p["source"],
            }
            for p in m["participants"]
        ]


def start_meeting(code: str, host_token: str) -> bool:
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return False
        m["status"] = "recording"
        m["started_at"] = _now()
    return True


def end_meeting(code: str, host_token: str) -> bool:
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return False
        m["status"] = "ended"
        m["ended_at"] = _now()
    return True


def _public_view_for_host(m: dict) -> dict:
    """Includes the host_token so the desktop app can authenticate later."""
    return {
        "code": m["code"],
        "title": m["title"],
        "host_token": m["host_token"],
        "created_at": _format_created(m["created_at"]),
        "share_url": f"https://subunit.ai/meet/{m['code']}",
        "join_url": f"https://subunit.ai/meet/{m['code']}",
    }


def _human_relative(ts: int) -> str:
    """Lightweight 'gestartet vor X' rendering — keeps the client dumb."""
    delta = max(0, _now() - ts)
    if delta < 60:
        return "gerade eben"
    if delta < 3600:
        return f"vor {delta // 60} Min"
    if delta < 86400:
        return f"vor {delta // 3600} Std"
    return f"vor {delta // 86400} Tagen"
