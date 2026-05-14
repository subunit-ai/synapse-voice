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
from typing import Literal

import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

import accounts as _accounts
import cleanup as _cleanup_mod
import email_send as _email_send

MODEL_NAME = os.environ.get("WHISPER_MODEL", "large-v3-turbo")
DEVICE = os.environ.get("WHISPER_DEVICE", "auto")  # auto | cpu | cuda
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE", "auto")  # auto | float16 | int8
API_KEY = os.environ.get("TRANSCRIBE_API_KEY", "")
MAX_AUDIO_MB = int(os.environ.get("TRANSCRIBE_MAX_MB", "25"))

_model = None


def _load_model():
    global _model
    if _model is not None:
        return _model
    from faster_whisper import WhisperModel

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

    print(f"[transcribe] loading {MODEL_NAME} on {device}/{compute_type}", flush=True)
    _model = WhisperModel(MODEL_NAME, device=device, compute_type=compute_type)
    print(f"[transcribe] model ready", flush=True)
    return _model


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _accounts.init_db()
    _load_model()
    yield


app = FastAPI(
    title="transcribe.subunit.ai",
    version="0.3.0",
    description="Speech-to-text endpoint for Synapse Voice (DSGVO-konform, EU-hosted).",
    lifespan=lifespan,
)


def _resolve_caller(api_key_header: str | None) -> dict | None:
    """Return the account dict for a given API key, or None if it's the
    operator-level master key (still allowed). Raises 401 otherwise."""
    if api_key_header == API_KEY and API_KEY:
        return None  # operator key — allowed, no per-user account
    if api_key_header:
        acct = _accounts.lookup_by_key(api_key_header)
        if acct:
            return acct
    if not API_KEY:
        # Auth disabled entirely (legacy mode for local-dev)
        return None
    raise HTTPException(status_code=401, detail="invalid api key")


def _require_active_access(caller: dict | None) -> None:
    """For gated endpoints (/transcribe, /cleanup): check trial / Pro
    state. Operator key + auth-disabled both pass through."""
    if caller is None:
        return  # operator-level
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
        "endpoints": ["POST /v1/transcribe", "GET /v1/health"],
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
    x_api_key: str | None = Header(default=None),
):
    caller = _resolve_caller(x_api_key)
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
    model = _load_model()
    # v0.6.0: "auto" or empty language → let faster-whisper detect
    # the language per utterance.  Useful for mixed-language meetings.
    lang_arg = None if language in ("", "auto", None) else language
    segments, info = model.transcribe(
        audio,
        language=lang_arg,
        initial_prompt=initial_prompt,
        beam_size=5,
        vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
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

    return JSONResponse(
        {
            "text": text,
            "language": info.language,
            "duration_s": info.duration,
            "elapsed_s": round(elapsed, 3),
            "model": MODEL_NAME,
        }
    )


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
):
    caller = _resolve_caller(x_api_key)
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
    """Per-user collection name: deterministic, no PII in the name."""
    api_key = account.get("api_key", "")
    h = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return f"svoice-{h}"


@app.post("/v1/synapse/save")
async def synapse_save(
    req: SynapseSaveRequest,
    x_api_key: str | None = Header(default=None),
):
    """Voice → Synapse bridge.

    Forwards the transcript into the user's per-account Synapse
    collection on the internal memory-agent service. The user explicitly
    opts in via the Settings toggle — no transcript leaves the box
    unless this endpoint was called.

    Returns 204 on success (no content), 401 on bad key, 402 on expired
    trial, 502 if the upstream Synapse service is down.
    """
    caller = _resolve_caller(x_api_key)
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
async def account_info(x_api_key: str | None = Header(default=None)):
    caller = _resolve_caller(x_api_key)
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
async def account_upgrade_url(x_api_key: str | None = Header(default=None)):
    """Return the URL the desktop app should open in a browser when the
    user clicks Upgrade. Includes the email as a query param so a future
    Stripe Checkout integration can prefill it."""
    caller = _resolve_caller(x_api_key)
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
