"""transcribe.subunit.ai — FastAPI service for Synapse Voice.

Endpoints:
    POST /v1/transcribe              (audio → text via faster-whisper)
    POST /v1/cleanup                 (raw text → cleaned text via Claude Haiku)
    POST /v1/account/request-code    (email → 6-digit code emailed via Resend)
    POST /v1/account/verify-code     (email + code → api_key, creates account)
    POST /v1/account/sign-up         (DEPRECATED v0.5.0 — direct, unverified)
    GET  /v1/account/info            (X-API-Key → email, plan, usage stats)
    GET  /v1/health
    GET  /

Auth: per-user X-API-Key header (issued by sign-up endpoint).
A single SERVER-WIDE TRANSCRIBE_API_KEY env var is also accepted for
operator-level access (used by the desktop app's "raw" subunit mode
when the user hasn't logged in yet).
"""
from __future__ import annotations

import io
import os
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

import accounts as _accounts
import cleanup as _cleanup_mod
import diarize_endpoint as _diarize_mod
import meet_endpoints as _meet_mod
import email_send as _email_send

MODEL_NAME = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
# 2026-05-16: Quality vs Fast modes. Quality = the default MODEL_NAME
# (large-v3-turbo). Fast = distil-large-v3 for instant-paste feel on
# Sonar hotkey-release. Both are loaded lazily, then kept in memory.
FAST_MODEL_NAME = os.environ.get("WHISPER_FAST_MODEL", "distil-large-v3")
DEVICE = os.environ.get("WHISPER_DEVICE", "auto")  # auto | cpu | cuda
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "auto")  # auto | float16 | int8
API_KEY = os.environ.get("TRANSCRIBE_API_KEY", "")
MAX_AUDIO_MB = int(os.environ.get("TRANSCRIBE_MAX_MB", "25"))

_model = None       # quality (large-v3-turbo)
_fast_model = None  # fast (distil-large-v3)


def _resolve_device_compute():
    device = DEVICE
    compute_type = COMPUTE_TYPE
    if device == "auto":
        try:
            import torch

            if torch.cuda.is_available():
                device = "cuda"
                # FP16 only on Volta+ (compute capability 7.0+).
                # Pascal (GTX 10xx) lacks efficient FP16 → use int8.
                major, _ = torch.cuda.get_device_capability(0)
                compute_type = "float16" if major >= 7 else "int8"
            else:
                device, compute_type = "cpu", "int8"
        except ImportError:
            device, compute_type = "cpu", "int8"
    if compute_type == "auto":
        compute_type = "float16" if device == "cuda" else "int8"
    return device, compute_type


def _load_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel
    device, compute_type = _resolve_device_compute()
    print(f"[transcribe] loading {MODEL_NAME} (quality) on {device}/{compute_type}", flush=True)
    _model = WhisperModel(MODEL_NAME, device=device, compute_type=compute_type)
    print(f"[transcribe] quality model ready", flush=True)
    return _model


def _load_fast_model():
    """Lazy-load the Fast model (distil-large-v3). First /v1/transcribe
    call with quality_mode=fast triggers the download — keeps cold-boot
    cost on the quality path predictable, and skips entirely if no
    client ever asks for Fast."""
    global _fast_model
    if _fast_model is not None:
        return _fast_model
    from faster_whisper import WhisperModel
    device, compute_type = _resolve_device_compute()
    print(f"[transcribe] loading {FAST_MODEL_NAME} (fast) on {device}/{compute_type}", flush=True)
    try:
        _fast_model = WhisperModel(FAST_MODEL_NAME, device=device, compute_type=compute_type)
    except Exception as e:
        # If distil-large-v3 isn't available (network, model not pulled),
        # silently fall back to the quality model for "fast" requests so
        # the user still gets a transcript. Logged but not raised.
        print(f"[transcribe] fast model load FAILED ({e}); using quality fallback", flush=True)
        _fast_model = _load_model()
        return _fast_model
    print(f"[transcribe] fast model ready", flush=True)
    return _fast_model


def _model_for(quality_mode: str | None):
    """Pick the right loaded WhisperModel for a request. Default ('' or
    anything we don't recognise) = quality, so old clients without the
    quality_mode field keep their behavior."""
    mode = (quality_mode or "").strip().lower()
    if mode == "fast":
        return _load_fast_model()
    return _load_model()


async def _meeting_retention_loop():
    """Hourly sweep that enforces the 24h DSGVO retention promise printed
    in every recap email. Runs as long as the FastAPI app is up."""
    import asyncio
    while True:
        try:
            await asyncio.sleep(3600)  # every hour
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _meet_mod.purge_expired_meetings)
        except asyncio.CancelledError:
            break
        except Exception as e:  # noqa: BLE001
            print(f"[meet] retention sweep error: {e}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    _accounts.init_db()
    _load_model()
    # Best-effort initial sweep at startup so a server that was off
    # while meetings aged still drops them on the next boot.
    try:
        _meet_mod.purge_expired_meetings()
    except Exception as e:  # noqa: BLE001
        print(f"[meet] startup purge failed: {e}", flush=True)
    sweep_task = asyncio.create_task(_meeting_retention_loop())
    try:
        yield
    finally:
        sweep_task.cancel()


app = FastAPI(
    title="transcribe.subunit.ai",
    version="0.3.0",
    description="Speech-to-text endpoint for Synapse Voice (DSGVO-konform, EU-hosted).",
    lifespan=lifespan,
)

# 2026-05-14: CORS for browser fetch from meet.subunit.ai → /v1/meetings/*.
# Until v0.9.0 we only need the meetings + diarize endpoints to be reachable
# from a browser; the Sonar desktop client doesn't hit CORS. Keep the list
# explicit so we don't accidentally widen the surface.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://meet.subunit.ai",
        "https://subunit.ai",
        "https://www.subunit.ai",
        # Local dev:
        "http://localhost:5173",
        "http://localhost:5174",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    max_age=600,
)


def _resolve_caller(
    api_key_header: str | None,
    authorization_header: str | None = None,
) -> dict | None:
    """Return the account dict for a given credential, or None for operator.

    Priority:
      1. Authorization: Bearer <subunit-jwt>   (v0.9.5, via auth.subunit.ai)
      2. X-API-Key: <per-user-key>             (legacy / first-party)
      3. X-API-Key: <operator-master-key>      (treated as None, no account)

    Raises 401 if all credentials are missing/invalid and auth isn't disabled.
    """
    # 1. Subunit-JWT (preferred)
    if authorization_header and authorization_header.lower().startswith("bearer "):
        from jwt_verify import verify_subunit_jwt, fetch_workspace_tier
        token = authorization_header[7:].strip()
        try:
            claims = verify_subunit_jwt(token)
        except ValueError as e:
            raise HTTPException(status_code=401, detail=f"invalid subunit token: {e}")

        is_operator = claims.get("is_operator", False)

        # 2026-05-16 (Codex P1): the JWT only proves identity. Query the
        # auth-server for the workspace's actual tier so an operator
        # changing the tier in the admin panel propagates within ~60s and
        # a fresh signup doesn't accidentally get gated access.
        plan = "operator" if is_operator else "free"  # safe default
        workspace_id = claims.get("ws")
        if not is_operator:
            ws = fetch_workspace_tier(token)
            if ws and ws.get("tier"):
                plan = ws["tier"].lower()
                # Prefer the auth-server's view of the active workspace.
                workspace_id = ws.get("id") or workspace_id

        # 2026-05-16 (Codex P1): backwards-compat for legacy api_key →
        # JWT migration. If a legacy account exists for the same email
        # we surface its api_key so /v1/synapse/save keeps writing to
        # the SAME ChromaDB collection (otherwise the user's history
        # would split between old and new collections).
        legacy_api_key = None
        email_for_legacy = (claims.get("email") or "").strip().lower()
        if email_for_legacy:
            try:
                acct = _accounts.lookup_by_email(email_for_legacy)
                if acct:
                    legacy_api_key = acct.get("api_key")
            except Exception:
                # lookup_by_email is best-effort — never block auth on it.
                pass

        # Build a caller dict that has the same shape as legacy accounts so
        # the rest of the codebase (record_usage, has_active_access etc.)
        # doesn't have to special-case JWT callers.
        return {
            "auth_kind":  "subunit_jwt",
            "user_id":    claims["sub"],
            "email":      claims.get("email", ""),
            "workspace":  workspace_id,
            "is_operator": is_operator,
            "plan":       plan,
            # Set to the legacy api_key when one exists — collection-namespace
            # continuity for migrating users. Stays None for fresh accounts.
            "api_key":    legacy_api_key,
        }

    # 2. Legacy per-user API key
    if api_key_header == API_KEY and API_KEY:
        return None  # operator key — allowed, no per-user account
    if api_key_header:
        acct = _accounts.lookup_by_key(api_key_header)
        if acct:
            return acct
    if not API_KEY:
        return None  # auth disabled entirely (legacy local-dev)
    raise HTTPException(status_code=401, detail="invalid credentials (need Bearer or X-API-Key)")


def _require_active_access(caller: dict | None) -> None:
    """For gated endpoints (/transcribe, /cleanup): check trial / Pro
    state. Operator key + auth-disabled both pass through."""
    if caller is None:
        return  # operator-level
    # JWT-authed callers (auth.subunit.ai): the auth server is the source
    # of truth for plan/tier — we fetched the workspace tier in
    # _resolve_caller and set caller["plan"] to its lowercase value. Map
    # that to has-access via the same tier list jwt_verify uses, so
    # admin-panel tier changes propagate to gated endpoints.
    if caller.get("auth_kind") == "subunit_jwt":
        if caller.get("is_operator"):
            return
        from jwt_verify import tier_has_active_access
        if tier_has_active_access(caller.get("plan")):
            return
        raise HTTPException(
            status_code=402,
            detail={
                "error": "tier_inactive",
                "message": (
                    "Your Subunit workspace is on the "
                    f"\"{caller.get('plan', 'free')}\" tier. Cloud transcription "
                    "needs Pro or higher — ask an operator to upgrade your "
                    "workspace."
                ),
                "plan": caller.get("plan", "free"),
            },
        )
    if not _accounts.has_active_access(caller):
        raise HTTPException(
            status_code=402,
            detail={
                "error": "trial_expired",
                "message": (
                    "Your free trial has ended. Upgrade to Pro to keep "
                    "using cloud transcription."
                ),
                "plan": caller.get("plan", "free"),
            },
        )


@app.get("/")
async def root():
    return {
        "service": "transcribe.subunit.ai",
        "version": "0.1.0",
        "model": MODEL_NAME,
        "endpoints": [
            "POST /v1/transcribe", "POST /v1/cleanup", "POST /v1/diarize",
            "POST /v1/meetings", "GET /v1/meetings/<code>/info",
            "POST /v1/meetings/<code>/join",
            "GET /v1/health",
        ],
    }


@app.get("/v1/health")
async def health():
    return {
        "status": "ok",
        "model_loaded": _model is not None,
        "model": MODEL_NAME,
    }


@app.post("/v1/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("de"),
    prompt: str = Form(""),
    with_segments: bool = Form(False),
    quality_mode: str = Form("quality"),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    caller = _resolve_caller(x_api_key, authorization)
    _require_active_access(caller)

    payload = await file.read()
    if len(payload) > MAX_AUDIO_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"audio too large (max {MAX_AUDIO_MB}MB)",
        )
    if not payload:
        raise HTTPException(status_code=400, detail="empty audio")

    audio = _decode_audio(payload, file.filename or "audio.wav")
    if audio.size == 0:
        raise HTTPException(status_code=400, detail="could not decode audio")

    # Whisper uses initial_prompt to bias toward custom vocab (names,
    # acronyms, jargon). Cap to 1 KiB so a malicious caller can't smuggle
    # a 10MB string into the encoder.
    initial_prompt = (prompt or "").strip()[:1024] or None

    t0 = time.time()
    # 2026-05-16: quality_mode picks between large-v3-turbo (quality,
    # default) and distil-large-v3 (fast). Distil is ~3-6x faster on
    # short-form dictation with negligible accuracy loss.
    model = _model_for(quality_mode)
    used_model = FAST_MODEL_NAME if (quality_mode or "").strip().lower() == "fast" else MODEL_NAME
    # v0.6.0: "auto" or empty language → let faster-whisper detect
    # the language per utterance.  Useful for mixed-language meetings.
    lang_arg = None if language in ("", "auto", None) else language
    # Fast mode trades beam_size=1 for latency — beam_size=5 is the
    # quality default. Together with the smaller distil model this is
    # what gets us the "instant paste" feel TJ asked for.
    is_fast = (quality_mode or "").strip().lower() == "fast"
    segments_iter, info = model.transcribe(
        audio,
        language=lang_arg,
        initial_prompt=initial_prompt,
        beam_size=1 if is_fast else 5,
        vad_filter=True,
    )
    # 2026-05-14: materialise segments so we can both join text AND
    # return them with timestamps when the client asks (v0.8.0 diarization).
    materialised = [
        {"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
        for s in segments_iter
    ]
    text = " ".join(s["text"] for s in materialised if s["text"]).strip()
    elapsed = time.time() - t0
    # Don't log transcript content — these are user dictations and may
    # contain passwords / PII / IP. Size + duration is enough for ops.
    print(
        f"[transcribe] {len(payload) / 1024:.1f}KB · {info.duration:.1f}s · "
        f"{elapsed:.2f}s · {len(text)}ch",
        flush=True,
    )

    if caller is not None:
        try:
            _accounts.record_usage(caller["api_key"], "transcribe", float(info.duration))
        except Exception:
            pass

    response = {
        "text": text,
        "language": info.language,
        "duration_s": info.duration,
        "elapsed_s": round(elapsed, 3),
        "model": used_model,
        "quality_mode": "fast" if is_fast else "quality",
    }
    if with_segments:
        response["segments"] = materialised
    return JSONResponse(response)


# ── Cleanup ────────────────────────────────────────────────────────────────

class CleanupRequest(BaseModel):
    text: str
    style: Literal[
        "tidy", "formal", "prompt", "email", "slack",
        "summary", "action_items", "minutes", "decisions", "recap_email",
        "raw",
    ] = "tidy"


@app.post("/v1/cleanup")
async def cleanup_endpoint(
    req: CleanupRequest,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    caller = _resolve_caller(x_api_key, authorization)
    _require_active_access(caller)

    if not req.text.strip():
        return JSONResponse({"text": req.text, "style": req.style})

    try:
        cleaned = await _cleanup_mod.cleanup(req.text, style=req.style)
    except _cleanup_mod.CleanupError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if caller is not None:
        try:
            _accounts.record_usage(caller["api_key"], "cleanup", 0.0)
        except Exception:
            pass

    return JSONResponse({"text": cleaned, "style": req.style})


# ── Diarization ───────────────────────────────────────────────────────────
#
# 2026-05-14 (codex top-1 priority): speaker-tagged meeting transcripts.
# Bundling diarize+torch into the desktop app would balloon Sonar from
# 214MB → 1GB+, so we run it server-side on the GPU host instead. The
# DSGVO surface stays the same as cloud Whisper: audio reaches Hamburg,
# is processed in-memory, no retention.

MAX_DIARIZE_MB = int(os.environ.get("MAX_DIARIZE_MB", "200"))


@app.post("/v1/diarize")
async def diarize_endpoint(
    file: UploadFile = File(...),
    num_speakers: int | None = Form(default=None),
    max_speakers: int = Form(default=8),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    """Speaker diarization for long-form recordings.

    Returns a JSON array of {start_s, end_s, speaker} segments. The
    client merges these with Whisper segments on its side.
    """
    caller = _resolve_caller(x_api_key, authorization)
    _require_active_access(caller)

    payload = await file.read()
    if len(payload) > MAX_DIARIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail=f"audio too large (max {MAX_DIARIZE_MB}MB)",
        )
    if not payload:
        raise HTTPException(status_code=400, detail="empty audio")

    try:
        result = _diarize_mod.diarize_audio_bytes(
            payload,
            num_speakers=num_speakers,
            max_speakers=max_speakers,
        )
    except Exception as e:
        # diarize raises on corrupt audio / model load errors.
        raise HTTPException(status_code=502, detail=f"diarize_failed: {e}")

    print(
        f"[diarize] {len(payload) / 1024:.1f}KB · "
        f"{result['num_speakers']} speakers · "
        f"{len(result['segments'])} segments · "
        f"{result['elapsed_s']}s",
        flush=True,
    )

    if caller is not None:
        try:
            _accounts.record_usage(caller["api_key"], "diarize", float(result["elapsed_s"]))
        except Exception:
            pass

    return JSONResponse(result)


# ── Meetings (QR-Check-In foundation, MVP) ────────────────────────────────
#
# 2026-05-14 (codex top 4 + TJ killer-idea): consent-by-join meeting
# sessions. Sonar-Desktop posts /v1/meetings to allocate a code, then
# shows QR + numeric code. Participants visit meet.subunit.ai, scan or
# type the code, enter name + email, and the server registers them
# in the session. WebRTC streaming + per-stream recording comes in
# Phase 2.

class CreateMeetingRequest(BaseModel):
    host_name: str
    host_email: str | None = None
    title: str | None = None


class JoinMeetingRequest(BaseModel):
    name: str
    email: str
    source: str | None = "web"
    token: str | None = None  # reserved for shared-link auth in phase 2


@app.post("/v1/meetings")
async def create_meeting(
    req: CreateMeetingRequest,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    """Host creates a meeting session. Requires a Subunit API key
    (gated like /v1/transcribe — meetings are a paid feature surface)."""
    caller = _resolve_caller(x_api_key, authorization)
    _require_active_access(caller)
    if not req.host_name.strip():
        raise HTTPException(status_code=400, detail="host_name required")
    payload = _meet_mod.create_meeting(
        host_name=req.host_name,
        host_email=req.host_email,
        title=req.title,
        api_key=caller["api_key"] if caller else None,
    )
    print(
        f"[meet] created code={payload['code']} host={req.host_name[:32]}",
        flush=True,
    )
    return JSONResponse(payload)


@app.get("/v1/meetings/{code}/info")
async def meeting_info(code: str):
    """Public lookup for the meet.subunit.ai landing page (no auth).
    Only returns non-sensitive fields: title, host name, status."""
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    info = _meet_mod.get_meeting_info(code)
    if not info:
        raise HTTPException(status_code=404, detail="meeting not found")
    return JSONResponse(info)


@app.post("/v1/meetings/{code}/join")
async def join_meeting(code: str, req: JoinMeetingRequest):
    """Guest joins. No auth — anyone with the code can join (that's
    the whole point). Returns a join_token the guest can use later to
    fetch their recap email."""
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    result = _meet_mod.join_meeting(
        code,
        name=req.name,
        email=req.email,
        source=req.source or "web",
    )
    if not result:
        raise HTTPException(status_code=400, detail="cannot join (meeting ended or invalid name/email)")
    print(
        f"[meet] join code={code} name={req.name[:32]} src={req.source}",
        flush=True,
    )
    return JSONResponse(result)


@app.post("/v1/meetings/{code}/participants/{participant_token}/approve")
async def approve_participant(code: str, participant_token: str, host_token: str = ""):
    """Host marks a pending guest as approved → they can now stream + read transcript."""
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    if not host_token:
        raise HTTPException(status_code=401, detail="host_token required")
    if not _meet_mod.approve_participant(code, host_token, participant_token):
        raise HTTPException(status_code=403, detail="invalid host_token or participant not found")
    return JSONResponse({"ok": True})


@app.post("/v1/meetings/{code}/participants/{participant_token}/reject")
async def reject_participant(code: str, participant_token: str, host_token: str = ""):
    """Host kicks a participant — drops their record and forces WS-disconnect."""
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    if not host_token:
        raise HTTPException(status_code=401, detail="host_token required")
    if not _meet_mod.reject_participant(code, host_token, participant_token):
        raise HTTPException(status_code=403, detail="invalid host_token or participant not found")
    return JSONResponse({"ok": True})


@app.get("/v1/meetings/{code}/me")
async def participant_self_status(code: str, t: str = ""):
    """Public lookup so guests can poll their own pending/approved state
    while sitting on the waiting screen. `t` = the join_token returned
    at /join time. Returns 404 if the participant was rejected."""
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    if not t:
        raise HTTPException(status_code=401, detail="token required")
    info = _meet_mod.get_participant_status(code, t)
    if not info:
        raise HTTPException(status_code=404, detail="participant not found (rejected or expired)")
    return JSONResponse(info)


@app.get("/v1/meetings/{code}/participants")
async def list_participants(
    code: str,
    host_token: str = "",
):
    """Host polls for the live check-in list. Authenticated via
    host_token (returned at meeting creation)."""
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    if not host_token:
        raise HTTPException(status_code=401, detail="host_token required")
    participants = _meet_mod.list_participants(code, host_token)
    if participants is None:
        raise HTTPException(status_code=403, detail="invalid host_token or meeting not found")
    return JSONResponse({"participants": participants, "count": len(participants)})


@app.get("/v1/meetings/{code}/transcript")
async def meeting_transcript(code: str, t: str = ""):
    """Return the rendered meeting transcript. Gated by either a valid
    participant join_token OR the host_token — both come from email
    magic-link or in-app context. The token is passed as `?t=…` so the
    PWA can embed it in a single URL."""
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    if not t:
        raise HTTPException(status_code=401, detail="token required")
    full = _meet_mod.get_meeting_full(code)
    if not full:
        raise HTTPException(status_code=404, detail="meeting not found")
    is_host = (t == full.get("host_token"))
    # Codex v0.9.2 #1 fix: pending guests can't see the transcript
    # either. Only approved (pending=false) participants count.
    is_guest = any(
        p.get("token") == t and not p.get("pending", False)
        for p in full.get("participants", [])
    )
    if not (is_host or is_guest):
        raise HTTPException(status_code=403, detail="invalid token")
    transcript_path = _meet_mod.meeting_audio_dir(code) / "transcript.md"
    minutes_path = _meet_mod.meeting_audio_dir(code) / "minutes.md"
    if not transcript_path.exists():
        return JSONResponse({
            "ready": False,
            "status": full.get("status", "open"),
            "title": full.get("title", ""),
            "host_name": full.get("host_name", ""),
        })
    return JSONResponse({
        "ready": True,
        "status": full.get("status", "ended"),
        "title": full.get("title", ""),
        "host_name": full.get("host_name", ""),
        "transcript_markdown": transcript_path.read_text(encoding="utf-8"),
        "minutes_markdown": (
            minutes_path.read_text(encoding="utf-8") if minutes_path.exists() else None
        ),
    })


@app.post("/v1/meetings/{code}/start")
async def start_meeting(code: str, host_token: str = ""):
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    if not _meet_mod.start_meeting(code, host_token):
        raise HTTPException(status_code=403, detail="invalid host_token or meeting not found")
    return JSONResponse({"ok": True, "status": "recording"})


@app.post("/v1/meetings/{code}/end")
async def end_meeting(code: str, host_token: str = ""):
    if not code.isdigit() or len(code) != 6:
        raise HTTPException(status_code=400, detail="invalid code")
    if not _meet_mod.end_meeting(code, host_token):
        raise HTTPException(status_code=403, detail="invalid host_token or meeting not found")
    # Kick off post-meeting processing in the background — Whisper per
    # participant audio file, merge with QR-Check-In names, deliver
    # per-participant magic-link recap emails.
    import asyncio
    asyncio.create_task(_post_meeting_pipeline(code))
    return JSONResponse({"ok": True, "status": "ended"})


# ── WebSocket: per-participant audio streaming ────────────────────────────
#
# 2026-05-14: WebSocket-based audio capture instead of full WebRTC. The
# PWA's MediaRecorder produces 1s WebM/Opus chunks; we append them to a
# per-participant file. No NAT traversal / STUN / TURN needed — runs
# over HTTPS like everything else.

@app.websocket("/v1/meetings/{code}/audio/{join_token}")
async def meeting_audio_stream(ws: WebSocket, code: str, join_token: str):
    """Receive WebM/Opus audio chunks from a single participant.

    Codex review v0.9.2 #2 (CRITICAL): refuse uploads if the meeting
    isn't actively recording, and refuse a second concurrent socket on
    the same token (avoids two writers appending to one webm file)."""
    if not code.isdigit() or len(code) != 6:
        await ws.close(code=4000)
        return
    found = _meet_mod.find_participant(code, join_token)
    if not found:
        await ws.close(code=4001)
        return
    meeting = found["meeting"]
    participant = found["participant"]
    # Codex review v0.9.2 #1 fix (v0.9.4): pending guests can't stream
    # until the host approves them from the desktop dialog.
    if participant.get("pending"):
        await ws.close(code=4005)  # "waiting on host approval"
        return
    if meeting.get("status") not in ("open", "recording"):
        # ended / purged → no more writes ever
        await ws.close(code=4003)
        return
    if not _meet_mod.claim_audio_socket(code, join_token):
        # someone else is already streaming under this token
        await ws.close(code=4004)
        return
    file_path = _meet_mod.audio_path_for(code, join_token)
    await ws.accept()
    print(
        f"[meet:ws] {code} {participant['name'][:24]} connected, "
        f"writing → {file_path}",
        flush=True,
    )
    # Codex review v0.9.2 #5: capture wall-clock offset between meeting
    # start and this participant's first sample, so the post-pipeline can
    # render correct chronology across speakers (Whisper segment times
    # are relative to each speaker's file, all starting at 0.0).
    started_at_meeting = meeting.get("started_at") or meeting.get("created_at") or _meet_mod._now()
    participant_offset_s = max(0.0, _meet_mod._now() - started_at_meeting)
    _meet_mod.record_audio_offset(code, join_token, participant_offset_s)
    bytes_written = 0
    try:
        with file_path.open("ab") as f:
            while True:
                msg = await ws.receive()
                # FastAPI gives us either {'bytes': b'...'} or {'text': '...'}.
                # We expect binary chunks; text frames are control messages.
                if msg.get("type") == "websocket.disconnect":
                    break
                if "bytes" in msg and msg["bytes"]:
                    f.write(msg["bytes"])
                    bytes_written += len(msg["bytes"])
                    # Flush every 32KB so a crash doesn't lose minutes.
                    if bytes_written % 32_768 < len(msg["bytes"]):
                        f.flush()
                elif "text" in msg:
                    # Control frame — currently just "stop" + "ping".
                    if msg["text"] == "stop":
                        break
                # Codex #2: stop accepting bytes the instant the host
                # ends the meeting — even if the client doesn't drop us.
                latest = _meet_mod._MEETINGS.get(code) or {}
                if latest.get("status") not in ("open", "recording"):
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[meet:ws] {code} {join_token[:8]} error: {e}", flush=True)
    finally:
        _meet_mod.release_audio_socket(code, join_token)
        print(
            f"[meet:ws] {code} {participant['name'][:24]} done, "
            f"{bytes_written / 1024:.1f}KB",
            flush=True,
        )


def _decode_webm_to_wav(webm_path: Path) -> Optional[Path]:
    """ffmpeg-decode the per-participant WebM/Opus stream into a 16kHz mono
    WAV that faster-whisper can ingest. Returns the WAV path or None on
    failure (corrupt file, ffmpeg missing, etc.)."""
    import subprocess
    wav_path = webm_path.with_suffix(".wav")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(webm_path),
                "-ac", "1", "-ar", "16000",
                "-f", "wav", str(wav_path),
            ],
            capture_output=True, timeout=180,
        )
        if result.returncode != 0:
            print(f"[meet:post] ffmpeg failed for {webm_path.name}: "
                  f"{result.stderr.decode('utf-8', 'replace')[:200]}", flush=True)
            return None
        return wav_path if wav_path.exists() and wav_path.stat().st_size > 1024 else None
    except (OSError, subprocess.SubprocessError) as e:
        print(f"[meet:post] ffmpeg error: {e}", flush=True)
        return None


def _transcribe_file(wav_path: Path) -> list[dict]:
    """Run faster-whisper on a single WAV. Returns list of segment dicts
    with start/end/text."""
    model = _load_model()
    segments_iter, info = model.transcribe(
        str(wav_path),
        language=None,         # auto-detect per participant
        vad_filter=True,
        beam_size=1,
        condition_on_previous_text=False,
    )
    out = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if text:
            out.append({"start": float(seg.start), "end": float(seg.end), "text": text})
    return out


def _render_meeting_markdown(meeting: dict, per_speaker_segments: list[dict]) -> str:
    """Interleave all segments by global start-time, label each with the
    QR-Check-In name. Used as the body of the recap email.

    Codex review v0.9.2 #5 fix: each participant's Whisper segments are
    relative to their own file (all start at 0.0). We add the per-stream
    offset that was captured when the WS connected, so guests joining
    late no longer appear to have spoken at minute 0.
    """
    flat = []
    for entry in per_speaker_segments:
        offset = float(entry.get("offset_s") or 0.0)
        for seg in entry["segments"]:
            flat.append({
                **seg,
                "speaker": entry["name"],
                "global_start": float(seg["start"]) + offset,
            })
    flat.sort(key=lambda s: s["global_start"])
    lines = []
    current_speaker = None
    for seg in flat:
        if seg["speaker"] != current_speaker:
            current_speaker = seg["speaker"]
            lines.append(f"\n**{current_speaker}:**")
        ts = _fmt_ts(seg["global_start"])
        lines.append(f"  [{ts}] {seg['text']}")
    return "\n".join(lines).strip()


def _fmt_ts(s: float) -> str:
    mm = int(s // 60); ss = int(s % 60)
    return f"{mm:02d}:{ss:02d}"


def _run_post_meeting_sync(code: str) -> None:
    """Synchronous core of the post-meeting pipeline. Runs in a thread
    executor so the asyncio loop stays responsive."""
    meeting = _meet_mod.get_meeting_full(code)
    if not meeting:
        print(f"[meet:post] {code} meeting not found", flush=True)
        return

    audio_files = _meet_mod.list_participant_audio_files(code)
    if not audio_files:
        print(f"[meet:post] {code} no audio recorded — skipping email", flush=True)
        return
    print(f"[meet:post] {code} processing {len(audio_files)} audio files", flush=True)

    per_speaker = []
    for af in audio_files:
        webm = Path(af["path"])
        wav = _decode_webm_to_wav(webm)
        if not wav:
            print(f"[meet:post] {code} {af['name']}: decode failed, skipping", flush=True)
            continue
        try:
            segs = _transcribe_file(wav)
            print(f"[meet:post] {code} {af['name']}: {len(segs)} segments", flush=True)
            per_speaker.append({
                "name": af["name"],
                "email": af["email"],
                "segments": segs,
                "offset_s": af.get("offset_s") or 0.0,
            })
        except Exception as e:
            print(f"[meet:post] {code} {af['name']} transcribe error: {e}", flush=True)
        finally:
            try:
                wav.unlink(missing_ok=True)
            except OSError:
                pass

    if not per_speaker:
        print(f"[meet:post] {code} no successful transcripts — aborting", flush=True)
        return

    markdown = _render_meeting_markdown(meeting, per_speaker)

    # Persist transcript BEFORE sending mails so the recap links work
    # the moment the mail lands — no race with a slow-decoder run.
    try:
        out = _meet_mod.meeting_audio_dir(code) / "transcript.md"
        out.write_text(markdown, encoding="utf-8")
    except OSError as e:
        print(f"[meet:post] {code} could not persist transcript.md: {e}", flush=True)

    # Codex Top 3: trustworthy meeting memory means tasks/decisions
    # are inline in the mail, not just the raw transcript. Call the
    # /minutes/ cleanup-style prompt against the merged markdown.
    summary_text: str | None = None
    try:
        import asyncio as _asyncio
        summary_text = _asyncio.run(_cleanup_mod.cleanup(markdown, style="minutes"))
        if summary_text and summary_text.strip():
            # Persist the structured protocol alongside the raw transcript.
            try:
                (_meet_mod.meeting_audio_dir(code) / "minutes.md").write_text(
                    summary_text, encoding="utf-8"
                )
            except OSError:
                pass
        else:
            summary_text = None
        print(f"[meet:post] {code} minutes generated ({len(summary_text or '')} chars)", flush=True)
    except Exception as e:  # noqa: BLE001 — cleanup is best-effort
        print(f"[meet:post] {code} minutes generation failed: {e}", flush=True)
        summary_text = None

    # Per-recipient recap mail. Each guest gets THEIR join_token in the
    # CTA URL so meet.subunit.ai/<code>?t=… can fetch the transcript
    # without a second auth step. Host gets the host_token.
    token_by_email = {p["email"]: p["token"] for p in
                      _meet_mod.list_participant_audio_files_with_tokens(code)}
    recipients = [{"name": p["name"], "email": p["email"], "token": token_by_email.get(p["email"], "")}
                  for p in per_speaker]
    if meeting.get("host_email"):
        recipients.append({
            "name": meeting["host_name"],
            "email": meeting["host_email"],
            "token": meeting.get("host_token", ""),
        })

    for r in recipients:
        try:
            _email_send.send_meeting_recap(
                to_email=r["email"],
                recipient_name=r["name"],
                host_name=meeting["host_name"],
                meeting_title=meeting["title"],
                code=code,
                transcript_markdown=markdown,
                recap_token=r.get("token") or None,
                summary_text=summary_text,
            )
            print(f"[meet:post] {code} recap → {r['email']}", flush=True)
        except _email_send.EmailDeliveryError as e:
            print(f"[meet:post] {code} email to {r['email']} failed: {e}", flush=True)

async def _post_meeting_pipeline(code: str) -> None:
    """After a meeting ends: transcribe each participant audio with the
    speaker label = QR Check-In name, build a merged Markdown protocol,
    deliver per-participant magic-link recap emails.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _run_post_meeting_sync, code)


# ── Subunit Suite Integration: Voice → Synapse Knowledge Base ─────────────

import hashlib
import urllib.request
import urllib.error
import json as _json

# Memory-agent inside the Subunit docker network. Internal — never
# reachable from outside this container.
SYNAPSE_INGEST_URL = os.environ.get(
    "SYNAPSE_INGEST_URL", "http://memory-agent:8001/ingest"
)
SYNAPSE_MAX_CHARS = 16_000  # ~3000 tokens — guards against accidental dumps


class SynapseSaveRequest(BaseModel):
    text: str
    # Optional context for richer search results in Synapse.
    window_title: str | None = None
    cleanup_style: str | None = None
    language: str | None = None
    transcribed_at: int | None = None


def _synapse_collection_for(account: dict) -> str:
    """Per-user collection name: deterministic, no PII in the name.

    For JWT-authed callers (no local api_key) we hash the auth-server
    `sub` (user_id) instead, so the same user keeps the same collection
    whether they hit us via legacy key or via auth.subunit.ai.
    """
    ident = account.get("api_key") or account.get("user_id") or ""
    h = hashlib.sha256(str(ident).encode("utf-8")).hexdigest()[:16]
    return f"svoice-{h}"


@app.post("/v1/synapse/save")
async def synapse_save(
    req: SynapseSaveRequest,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    """Voice → Synapse bridge.

    Forwards the transcript into the user's per-account Synapse
    collection on the internal memory-agent service. The user explicitly
    opts in via the Settings toggle — no transcript leaves the box
    unless this endpoint was called.

    Returns 204 on success (no content), 401 on bad key, 402 on expired
    trial, 502 if the upstream Synapse service is down.
    """
    caller = _resolve_caller(x_api_key, authorization)
    if caller is None:
        # Operator key cannot save — they have no per-user collection.
        raise HTTPException(status_code=400, detail="account-scoped endpoint")
    _require_active_access(caller)

    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="empty text")
    if len(text) > SYNAPSE_MAX_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"text too long ({len(text)} chars; max {SYNAPSE_MAX_CHARS})",
        )

    payload = {
        "text": text,
        "source": "synapse-voice",
        "category": "synapse-voice",
        "metadata": {
            "collection": _synapse_collection_for(caller),
            "account_email": caller.get("email", ""),
            "window_title": (req.window_title or "")[:200],
            "cleanup_style": req.cleanup_style or "",
            "language": req.language or "",
            "transcribed_at": req.transcribed_at or int(time.time()),
        },
    }

    try:
        request = urllib.request.Request(
            SYNAPSE_INGEST_URL,
            data=_json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=8) as r:
            r.read()  # drain
    except urllib.error.URLError as e:
        raise HTTPException(status_code=502, detail=f"synapse upstream: {e}")
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return JSONResponse(
        {"saved": True, "collection": payload["metadata"]["collection"]},
        status_code=200,
    )


# ── Account ────────────────────────────────────────────────────────────────

class SignUpRequest(BaseModel):
    email: EmailStr


@app.post("/v1/account/sign-up")
async def account_sign_up(req: SignUpRequest):
    """DEPRECATED in v0.5.0 — kept for backwards-compat with v0.4.x clients.

    The unverified flow lets anyone register an arbitrary email and get a
    fresh API key without any proof of mailbox ownership.  v0.5.0 clients
    use /request-code → /verify-code instead.
    """
    try:
        acct = _accounts.create_account(str(req.email))
    except _accounts.EmailAlreadyRegistered:
        raise HTTPException(
            status_code=409,
            detail=(
                "email already registered — to recover a lost API key, "
                "contact support@subunit.ai"
            ),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(
        {
            "email": acct["email"],
            "api_key": acct["api_key"],
            "plan": acct["plan"],
            "is_new": True,
        }
    )


# ── Email-verified signup (v0.5.0) ─────────────────────────────────────


class RequestCodeRequest(BaseModel):
    email: EmailStr


class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str


@app.post("/v1/account/request-code")
async def account_request_code(req: RequestCodeRequest):
    """Step 1 of the email-verified signup flow.

    Issues a fresh 6-digit code, stores its hash, and emails the code via
    Resend.  The plaintext code never leaves this function.
    """
    email_str = str(req.email)
    # Cheap housekeeping — keeps the pending table from growing.
    try:
        _accounts.purge_expired_pending_signups()
    except Exception:
        pass
    try:
        code, ttl = _accounts.request_signup_code(email_str)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _accounts.EmailAlreadyRegistered:
        raise HTTPException(
            status_code=409,
            detail=(
                "email already registered — to recover a lost API key, "
                "contact support@subunit.ai"
            ),
        )
    except _accounts.SignupCodeRateLimited as e:
        # 429 + Retry-After lets the client show "try again in Xs" cleanly.
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "retry_after": e.retry_after},
            headers={"Retry-After": str(e.retry_after)},
        )

    try:
        _email_send.send_verification_code(email_str, code)
    except _email_send.EmailDeliveryError as e:
        # The code is already stored — but if we couldn't deliver it the
        # user will never get past step 2.  502 so the client retries.
        raise HTTPException(status_code=502, detail=f"email delivery failed: {e}")

    return JSONResponse(
        {
            "sent": True,
            "ttl_seconds": ttl,
            "resend_cooldown_seconds": _accounts.SIGNUP_RESEND_COOLDOWN,
        }
    )


@app.post("/v1/account/verify-code")
async def account_verify_code(req: VerifyCodeRequest):
    """Step 2 of the email-verified signup flow.

    On match: creates the account, returns the api_key + 7-day Pro trial.
    On mismatch: 400 with `attempts_remaining` so the UI can show a
    counter; after 5 wrong attempts the row locks until the user hits
    /request-code again (which resets the counter).
    """
    try:
        acct = _accounts.verify_signup_code(str(req.email), req.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except _accounts.SignupCodeNotFound:
        raise HTTPException(
            status_code=404,
            detail="no pending signup for this email — request a code first",
        )
    except _accounts.SignupCodeExpired:
        raise HTTPException(
            status_code=410,
            detail="code expired — request a fresh one",
        )
    except _accounts.SignupCodeWrong as e:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "wrong_code",
                "attempts_remaining": e.attempts_remaining,
            },
        )
    except _accounts.SignupCodeLocked:
        raise HTTPException(
            status_code=429,
            detail="too many wrong attempts — request a fresh code",
        )
    except _accounts.EmailAlreadyRegistered:
        raise HTTPException(
            status_code=409,
            detail="email already registered while verifying — try /info with the existing key",
        )
    return JSONResponse(
        {
            "email": acct["email"],
            "api_key": acct["api_key"],
            "plan": acct["plan"],
            "trial_started_at": acct["trial_started_at"],
            "trial_expires_at": acct["trial_expires_at"],
            "is_new": True,
        }
    )


@app.get("/v1/account/info")
async def account_info(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    caller = _resolve_caller(x_api_key, authorization)
    if caller is None:
        # Operator key or auth-disabled — no per-user info
        return JSONResponse({
            "email": None,
            "plan": "operator",
            "calls": 0,
            "audio_seconds": 0.0,
            "trial_expires_at": 0,
            "subscription_active_until": 0,
            "has_access": True,
        })
    # JWT-authed callers don't live in the local accounts DB — return a
    # synthetic info payload built from the auth-server tier. The plan
    # field reflects the actual workspace tier (free/basic/pro/...) and
    # has_access mirrors the same tier-based gate as /v1/transcribe.
    if caller.get("auth_kind") == "subunit_jwt":
        from jwt_verify import tier_has_active_access
        plan = caller.get("plan", "free")
        return JSONResponse({
            "email": caller.get("email", ""),
            "plan": plan,
            "calls": 0,
            "audio_seconds": 0.0,
            "trial_started_at": 0,
            "trial_expires_at": 0,
            "subscription_active_until": 0,
            "has_access": bool(caller.get("is_operator")) or tier_has_active_access(plan),
        })
    summary = _accounts.usage_summary(caller["api_key"])
    return JSONResponse(
        {
            "email": caller["email"],
            "plan": caller["plan"],
            "calls": summary["calls"],
            "audio_seconds": summary["audio_seconds"],
            "trial_started_at": caller.get("trial_started_at", 0),
            "trial_expires_at": _accounts.trial_expires_at(caller),
            "subscription_active_until": caller.get("subscription_active_until", 0),
            "has_access": _accounts.has_active_access(caller),
        }
    )


# Where to send a user when their trial expires. The actual checkout URL
# (Stripe / Lemon-Squeezy) is wired up later — for now we point at the
# pricing page which describes the plans, so the desktop app's paywall
# button has a real destination. Override via UPGRADE_URL env.
UPGRADE_URL = os.environ.get(
    "UPGRADE_URL", "https://transcribe.subunit.ai/pricing"
)


@app.get("/v1/account/upgrade-url")
async def account_upgrade_url(
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
):
    """Return the URL the desktop app should open in a browser when the
    user clicks Upgrade. Includes the email as a query param so a future
    Stripe Checkout integration can prefill it."""
    caller = _resolve_caller(x_api_key, authorization)
    suffix = ""
    if caller and caller.get("email"):
        from urllib.parse import urlencode
        suffix = "?" + urlencode({"email": caller["email"]})
    return JSONResponse({"url": UPGRADE_URL + suffix})


def _decode_audio(payload: bytes, filename: str) -> np.ndarray:
    """Decode payload to 16kHz mono float32. WAV-fast-path, ffmpeg-fallback for ogg/opus/etc."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".wav", ".wave") or payload[:4] == b"RIFF":
        return _decode_wav(payload)
    return _decode_via_ffmpeg(payload, suffix or ".bin")


def _decode_wav(payload: bytes) -> np.ndarray:
    try:
        with wave.open(io.BytesIO(payload), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            sample_width = wf.getsampwidth()
            channels = wf.getnchannels()
            framerate = wf.getframerate()
    except wave.Error:
        return _decode_via_ffmpeg(payload, ".wav")

    if sample_width == 2:
        audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif sample_width == 1:
        audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        return _decode_via_ffmpeg(payload, ".wav")

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    if framerate != 16000:
        audio = _resample(audio, framerate, 16000)
    return audio.astype(np.float32)


def _decode_via_ffmpeg(payload: bytes, suffix: str) -> np.ndarray:
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as src:
        src.write(payload)
        src_path = src.name
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error",
                "-i", src_path,
                "-f", "f32le",
                "-ac", "1",
                "-ar", "16000",
                "-",
            ],
            capture_output=True,
            check=False,
            timeout=60,
        )
        if result.returncode != 0:
            return np.zeros(0, dtype=np.float32)
        return np.frombuffer(result.stdout, dtype=np.float32)
    finally:
        try:
            os.unlink(src_path)
        except OSError:
            pass


def _resample(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio
    ratio = dst_rate / src_rate
    n_dst = int(len(audio) * ratio)
    if n_dst <= 0:
        return np.zeros(0, dtype=np.float32)
    x_src = np.linspace(0, 1, len(audio), endpoint=False)
    x_dst = np.linspace(0, 1, n_dst, endpoint=False)
    return np.interp(x_dst, x_src, audio).astype(np.float32)
