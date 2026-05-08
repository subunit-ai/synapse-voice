"""transcribe.subunit.ai — FastAPI service for Synapse Voice.

Endpoints:
    POST /v1/transcribe       (audio → text via faster-whisper)
    POST /v1/cleanup          (raw text → cleaned text via Claude Haiku)
    POST /v1/account/sign-up  (email → api_key, creates if new)
    GET  /v1/account/info     (X-API-Key → email, plan, usage stats)
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
    x_api_key: str | None = Header(default=None),
):
    caller = _resolve_caller(x_api_key)

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

    t0 = time.time()
    model = _load_model()
    segments, info = model.transcribe(
        audio,
        language=language or None,
        beam_size=5,
        vad_filter=True,
    )
    text = " ".join(seg.text.strip() for seg in segments).strip()
    elapsed = time.time() - t0
    print(
        f"[transcribe] {len(payload) / 1024:.1f}KB · {info.duration:.1f}s · "
        f"{elapsed:.2f}s · '{text[:60]}'",
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
    style: Literal["tidy", "formal", "raw"] = "tidy"


@app.post("/v1/cleanup")
async def cleanup_endpoint(
    req: CleanupRequest,
    x_api_key: str | None = Header(default=None),
):
    caller = _resolve_caller(x_api_key)

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


# ── Account ────────────────────────────────────────────────────────────────

class SignUpRequest(BaseModel):
    email: EmailStr


@app.post("/v1/account/sign-up")
async def account_sign_up(req: SignUpRequest):
    """Self-service onboarding — return an API key for an email.

    Idempotent: existing email returns the existing api_key (no need for a
    separate "recover" endpoint). The desktop app uses this both on first
    install and on re-login.
    """
    try:
        acct = _accounts.get_or_create(str(req.email))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(
        {
            "email": acct["email"],
            "api_key": acct["api_key"],
            "plan": acct["plan"],
            "is_new": acct["is_new"],
        }
    )


@app.get("/v1/account/info")
async def account_info(x_api_key: str | None = Header(default=None)):
    caller = _resolve_caller(x_api_key)
    if caller is None:
        # Operator key or auth-disabled — no per-user info
        return JSONResponse({"email": None, "plan": "operator", "calls": 0, "audio_seconds": 0.0})
    summary = _accounts.usage_summary(caller["api_key"])
    return JSONResponse(
        {
            "email": caller["email"],
            "plan": caller["plan"],
            "calls": summary["calls"],
            "audio_seconds": summary["audio_seconds"],
        }
    )


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
