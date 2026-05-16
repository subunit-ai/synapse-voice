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
from pathlib import Path
from typing import Optional


# Lock-protected in-memory store with JSON-file persistence. Each mutation
# writes the full store back atomically so meetings survive restarts. The
# proper Postgres migration happens once the WebRTC + per-stream-recording
# pipeline lands; the file store is fine for the session-management MVP.
_LOCK = threading.RLock()
_MEETINGS: dict[str, dict] = {}  # code → meeting dict
_STORE_PATH = Path(os.environ.get("MEET_STORE_PATH", "/data/meetings.json"))

# Codex review v0.9.2 #2 (CRITICAL): prevent two writers on the same
# .webm. Holds (code, join_token) for every currently-open audio
# WebSocket so a second connection with the same token is rejected.
_OPEN_AUDIO_SOCKETS: set[tuple[str, str]] = set()


def _load_from_disk() -> None:
    """Populate _MEETINGS from the JSON file at startup. Best-effort."""
    global _MEETINGS
    try:
        if _STORE_PATH.exists():
            with _STORE_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    _MEETINGS = data
                    print(f"[meet] loaded {len(_MEETINGS)} meetings from {_STORE_PATH}", flush=True)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[meet] could not load store: {e}", flush=True)


def _save_to_disk() -> None:
    """Snapshot _MEETINGS to disk. Called inside the lock for consistency."""
    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STORE_PATH.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(_MEETINGS, f, ensure_ascii=False, indent=0)
        tmp.replace(_STORE_PATH)
    except OSError as e:
        print(f"[meet] could not save store: {e}", flush=True)


_load_from_disk()


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
        _save_to_disk()
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
    """Guest join. Returns the participant record + a personal join_token.

    Codex review v0.9.2 #1 fix (v0.9.4): guests are created with
    `pending=true`. The audio + transcript endpoints both refuse the
    token until the host explicitly approves the participant. The
    host's own self-join (`source="host"`) is auto-approved — the
    host_token already authenticates them at creation time."""
    if not name.strip() or not email.strip() or "@" not in email:
        return None
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return None
        if m["status"] in ("ended", "purged"):
            return None
        token = _gen_token(24)
        # Hosts joining themselves get pending=false; everyone else waits
        # for host-approval. This matches the Zoom/Teams "Warteraum" UX.
        auto_approved = (source == "host")
        participant = {
            "token": token,
            "name": name.strip()[:80],
            "email": email.strip().lower()[:200],
            "joined_at": _now(),
            "source": source,  # web | qr | code | host
            "pending": not auto_approved,
        }
        m["participants"].append(participant)
        _save_to_disk()
        return {
            "join_token": token,
            "name": participant["name"],
            "meeting_title": m["title"],
            "host_name": m["host_name"],
            "code": code,
            "pending": participant["pending"],
        }


def approve_participant(code: str, host_token: str, participant_token: str) -> bool:
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return False
        for p in m["participants"]:
            if p["token"] == participant_token:
                p["pending"] = False
                p["approved_at"] = _now()
                _save_to_disk()
                return True
    return False


def reject_participant(code: str, host_token: str, participant_token: str) -> bool:
    """Hard reject — drop from list, kill any open WS via the claim set."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return False
        before = len(m["participants"])
        m["participants"] = [p for p in m["participants"] if p["token"] != participant_token]
        if len(m["participants"]) == before:
            return False
        _OPEN_AUDIO_SOCKETS.discard((code, participant_token))
        _save_to_disk()
    return True


def get_participant_status(code: str, participant_token: str) -> Optional[dict]:
    """Public — guest polls this to find out when the host approves them."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return None
        for p in m["participants"]:
            if p["token"] == participant_token:
                return {
                    "code": code,
                    "name": p["name"],
                    "pending": bool(p.get("pending", False)),
                    "meeting_title": m["title"],
                    "host_name": m["host_name"],
                    "meeting_status": m["status"],
                }
    return None


def list_participants(code: str, host_token: str) -> Optional[list[dict]]:
    """Host polls for the live participant list."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return None
        return [
            {
                "name": p["name"],
                "email": p["email"],  # host can see emails (their own meeting)
                "token": p["token"],  # host needs this to approve/reject
                "joined_at": _format_created(p["joined_at"]),
                "joined_at_relative": _human_relative(p["joined_at"]),
                "source": p["source"],
                "pending": bool(p.get("pending", False)),
            }
            for p in m["participants"]
        ]


def start_meeting(code: str, host_token: str) -> bool:
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return False
        # Codex review v0.9.2 secondary: re-starting an ended meeting
        # must clear ended_at, otherwise the next 24h sweep can purge
        # the live audio.
        if m.get("status") == "purged":
            return False  # cannot restart a purged meeting
        m["status"] = "recording"
        m["started_at"] = _now()
        m.pop("ended_at", None)
        _save_to_disk()
    return True


def end_meeting(code: str, host_token: str) -> bool:
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m or m["host_token"] != host_token:
            return False
        m["status"] = "ended"
        m["ended_at"] = _now()
        _save_to_disk()
    return True


def _public_view_for_host(m: dict) -> dict:
    """Includes the host_token so the desktop app can authenticate later."""
    return {
        "code": m["code"],
        "title": m["title"],
        "host_token": m["host_token"],
        "created_at": _format_created(m["created_at"]),
        "share_url": f"https://meet.subunit.ai/{m['code']}",
        "join_url": f"https://meet.subunit.ai/{m['code']}",
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


# ── Per-participant audio storage ─────────────────────────────────────────

_AUDIO_ROOT = Path(os.environ.get("MEET_AUDIO_ROOT", "/data/meeting-audio"))


def find_participant(code: str, join_token: str) -> Optional[dict]:
    """Resolve (code, join_token) → participant record. Used by the WS auth."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return None
        for p in m["participants"]:
            if p["token"] == join_token:
                return {"meeting": m, "participant": p}
    return None


def audio_path_for(code: str, join_token: str) -> Path:
    """Per-participant WebM file path. Created on first write."""
    d = _AUDIO_ROOT / code
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{join_token}.webm"


def meeting_audio_dir(code: str) -> Path:
    return _AUDIO_ROOT / code


def claim_audio_socket(code: str, join_token: str) -> bool:
    """Atomic check-and-claim that this (code, token) has no other open
    WebSocket. Returns True if we got the claim, False if someone else
    already holds it."""
    key = (code, join_token)
    with _LOCK:
        if key in _OPEN_AUDIO_SOCKETS:
            return False
        _OPEN_AUDIO_SOCKETS.add(key)
    return True


def release_audio_socket(code: str, join_token: str) -> None:
    """Drop the claim — call from the WS handler's `finally:` block."""
    with _LOCK:
        _OPEN_AUDIO_SOCKETS.discard((code, join_token))


def record_audio_offset(code: str, join_token: str, offset_seconds: float) -> None:
    """Per-participant offset between meeting-start and this stream's
    first byte. Used by the post-meeting pipeline to fix chronology
    across speakers (Codex review v0.9.2 #5)."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return
        for p in m["participants"]:
            if p["token"] == join_token:
                # Only record the FIRST offset — if a stream reconnects
                # (e.g. flaky cell network) we want the original start time.
                if "audio_offset_s" not in p:
                    p["audio_offset_s"] = float(offset_seconds)
                    _save_to_disk()
                return


def get_meeting_full(code: str) -> Optional[dict]:
    """Return the full meeting record (incl. host_email). Used by the
    post-meeting pipeline to address the recap email to the host."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return None
        return dict(m)  # shallow copy is fine — caller only reads


def list_participant_audio_files(code: str) -> list[dict]:
    """For post-meeting transcription: which participant has audio, where."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return []
        out = []
        for p in m["participants"]:
            path = audio_path_for(code, p["token"])
            if path.exists() and path.stat().st_size > 0:
                out.append({
                    "name": p["name"],
                    "email": p["email"],
                    "token": p["token"],
                    "path": str(path),
                    "size": path.stat().st_size,
                    # Codex v0.9.2 #5: wall-clock offset captured at
                    # WebSocket connect time. None for legacy streams.
                    "offset_s": float(p.get("audio_offset_s") or 0.0),
                })
        return out


def list_participant_audio_files_with_tokens(code: str) -> list[dict]:
    """Like list_participant_audio_files() but always includes the token —
    used by the post-meeting mail-out to embed a per-recipient recap link.
    For email→token lookup the post-pipeline doesn't care whether audio
    was actually recorded, only that the participant exists."""
    with _LOCK:
        m = _MEETINGS.get(code)
        if not m:
            return []
        return [
            {"name": p["name"], "email": p["email"], "token": p["token"]}
            for p in m["participants"]
        ]


# ── DSGVO 24h auto-delete ─────────────────────────────────────────────────
#
# The post-meeting recap email promises "Wir loeschen dein Audio in 24h
# automatisch (DSGVO)". This is the function that delivers that promise.
# Called by the background sweep loop in main.py once an hour.

import shutil


def purge_expired_meetings(retention_hours: float = 24.0) -> list[str]:
    """Delete audio + redact PII for meetings that ended longer ago than
    `retention_hours`. Returns the list of meeting codes affected.

    The meeting record itself is kept (code + title) so a returning user
    sees "Meeting beendet · vor X Tagen" instead of a 404, but every PII
    field (host_email, participant emails/names, transcript text, audio
    files) is removed."""
    now = _now()
    cutoff = now - int(retention_hours * 3600)
    purged: list[str] = []
    with _LOCK:
        for code, m in list(_MEETINGS.items()):
            ended = m.get("ended_at") or 0
            if not ended or ended > cutoff:
                continue
            if m.get("status") != "ended":
                # Forgotten meetings (never explicitly ended). Treat the
                # creation timestamp as the retention anchor — same rule.
                if (m.get("created_at") or now) > cutoff:
                    continue

            # Audio + minutes + transcript on disk → gone
            audio_dir = _AUDIO_ROOT / code
            try:
                if audio_dir.exists():
                    shutil.rmtree(audio_dir)
            except OSError as e:
                print(f"[meet] purge: could not remove {audio_dir}: {e}", flush=True)

            # PII redaction in the in-memory record. Codex secondary:
            # title can identify the meeting, tokens can be replayed
            # against the transcript endpoint until the record is
            # actually deleted — wipe all of them.
            m["host_email"] = None
            m["host_name"] = "(geloescht)"
            m["host_token"] = ""
            m["title"] = f"Meeting #{code}"
            m["status"] = "purged"
            m["purged_at"] = now
            for p in m.get("participants", []):
                p["email"] = ""
                p["name"] = "(geloescht)"
                p["token"] = ""
            purged.append(code)

        if purged:
            _save_to_disk()
            print(f"[meet] purged {len(purged)} meeting(s): {purged}", flush=True)
    return purged
