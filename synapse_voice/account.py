"""Subunit-server account API client.

Talks to /v1/account/* endpoints on transcribe.subunit.ai. Self-service
sign-up: user enters email, server returns api_key (creating the account
on first request).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from .logger import get as _get_logger

_log = _get_logger(__name__)


@dataclass
class Account:
    email: str
    api_key: str
    plan: str
    is_new: bool


@dataclass
class AccountInfo:
    email: Optional[str]
    plan: str
    calls: int
    audio_seconds: float
    trial_started_at: int = 0
    trial_expires_at: int = 0
    subscription_active_until: int = 0
    has_access: bool = True

    @property
    def trial_days_left(self) -> int:
        """How many days remain on the user's trial. Returns 0 if no
        trial is active. Negative trials clamp to 0."""
        import time as _t
        if not self.trial_expires_at:
            return 0
        secs = self.trial_expires_at - int(_t.time())
        return max(0, (secs + 86399) // 86400)  # ceil to whole days

    @property
    def is_trial(self) -> bool:
        return self.plan == "trial" and self.trial_expires_at > 0

    @property
    def is_pro(self) -> bool:
        import time as _t
        return self.plan == "pro" and self.subscription_active_until > int(_t.time())


def _account_base(transcribe_endpoint: str) -> str:
    """Derive the account base URL from the transcribe endpoint.

    transcribe_endpoint looks like https://transcribe.subunit.ai/v1/transcribe
    → base = https://transcribe.subunit.ai
    """
    base = transcribe_endpoint.rstrip("/")
    for marker in ("/v1/transcribe", "/v1"):
        if base.endswith(marker):
            base = base[: -len(marker)]
            break
    return base.rstrip("/")


def sign_up(transcribe_endpoint: str, email: str) -> Account:
    """DEPRECATED v0.5.0 — direct unverified signup.  Kept only for
    backward compat with v0.4.x clients.  New code should use
    :func:`request_code` + :func:`verify_code`."""
    base = _account_base(transcribe_endpoint)
    url = f"{base}/v1/account/sign-up"
    try:
        r = requests.post(url, json={"email": email}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return Account(
            email=data["email"],
            api_key=data["api_key"],
            plan=data.get("plan", "free"),
            is_new=bool(data.get("is_new", False)),
        )
    except requests.HTTPError as e:
        body = ""
        status = "?"
        try:
            status = str(e.response.status_code)
            body = e.response.text[:200]
        except Exception:
            pass
        _log.error("Account sign-up failed (%s): %s", status, body)
        raise RuntimeError(f"Sign-up failed: HTTP {status} {body}") from e
    except requests.RequestException as e:
        _log.error("Account sign-up network error: %s", e)
        raise RuntimeError(f"Sign-up failed: {e}") from e


# ── Email-verified signup (v0.5.0) ─────────────────────────────────────


@dataclass
class CodeRequestResult:
    """Returned by :func:`request_code` on success.  ``ttl_seconds`` is the
    code's validity window (typically 600s), ``resend_cooldown_seconds``
    is the minimum delay before the same email can request a fresh code."""

    sent: bool
    ttl_seconds: int
    resend_cooldown_seconds: int


class CodeError(RuntimeError):
    """Generic verification flow error.  Subclasses carry specific intent
    so the UI can show targeted messages."""


class EmailAlreadyRegistered(CodeError):
    """The email already has an account — direct user to recovery."""


class CodeRateLimited(CodeError):
    """A code was requested too soon after the previous one."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


class CodeWrong(CodeError):
    """The code didn't match.  ``attempts_remaining`` reflects how many
    more wrong tries are left before the row locks."""

    def __init__(self, attempts_remaining: int) -> None:
        super().__init__(f"{attempts_remaining} attempts remaining")
        self.attempts_remaining = attempts_remaining


class CodeExpired(CodeError):
    """The code's TTL elapsed.  User needs to request a fresh one."""


class CodeLocked(CodeError):
    """Too many wrong attempts — user must request a fresh code."""


class CodeNotFound(CodeError):
    """No pending signup for this email — user must request a code."""


class EmailDeliveryFailed(CodeError):
    """Server accepted the request but Resend rejected delivery."""


def request_code(transcribe_endpoint: str, email: str) -> CodeRequestResult:
    """Step 1 of the verified signup flow.  Asks the server to email a
    6-digit code to ``email``.  On success returns the TTL + cooldown so
    the UI can show a countdown.  Raises :class:`CodeError` subclasses
    on any user-facing failure."""
    base = _account_base(transcribe_endpoint)
    url = f"{base}/v1/account/request-code"
    try:
        r = requests.post(url, json={"email": email}, timeout=20)
    except requests.RequestException as e:
        _log.error("request_code network error: %s", e)
        raise CodeError(f"network error: {e}") from e

    if r.status_code == 200:
        data = r.json()
        return CodeRequestResult(
            sent=bool(data.get("sent", False)),
            ttl_seconds=int(data.get("ttl_seconds", 600)),
            resend_cooldown_seconds=int(data.get("resend_cooldown_seconds", 30)),
        )
    if r.status_code == 409:
        raise EmailAlreadyRegistered(email)
    if r.status_code == 429:
        try:
            retry_after = int(r.json().get("retry_after", 30))
        except Exception:
            retry_after = 30
        raise CodeRateLimited(retry_after)
    if r.status_code == 502:
        raise EmailDeliveryFailed("the server couldn't deliver the code")
    raise CodeError(f"server error: HTTP {r.status_code} {r.text[:120]}")


def verify_code(transcribe_endpoint: str, email: str, code: str) -> Account:
    """Step 2 of the verified signup flow.  On match: server creates the
    account and returns the api_key.  Raises a specific
    :class:`CodeError` subclass on every failure mode the UI cares
    about."""
    base = _account_base(transcribe_endpoint)
    url = f"{base}/v1/account/verify-code"
    try:
        r = requests.post(
            url, json={"email": email, "code": code}, timeout=15
        )
    except requests.RequestException as e:
        _log.error("verify_code network error: %s", e)
        raise CodeError(f"network error: {e}") from e

    if r.status_code == 200:
        data = r.json()
        return Account(
            email=data["email"],
            api_key=data["api_key"],
            plan=data.get("plan", "trial"),
            is_new=bool(data.get("is_new", True)),
        )
    if r.status_code == 400:
        # Two flavours — bad shape ("invalid email") or wrong_code.
        try:
            payload = r.json().get("detail")
            if isinstance(payload, dict) and payload.get("error") == "wrong_code":
                raise CodeWrong(int(payload.get("attempts_remaining", 0)))
        except CodeError:
            raise
        except Exception:
            pass
        raise CodeError(f"bad request: {r.text[:120]}")
    if r.status_code == 404:
        raise CodeNotFound(email)
    if r.status_code == 410:
        raise CodeExpired()
    if r.status_code == 429:
        raise CodeLocked()
    if r.status_code == 409:
        raise EmailAlreadyRegistered(email)
    raise CodeError(f"server error: HTTP {r.status_code} {r.text[:120]}")


def info(transcribe_endpoint: str, api_key: str) -> Optional[AccountInfo]:
    if not api_key:
        return None
    base = _account_base(transcribe_endpoint)
    url = f"{base}/v1/account/info"
    try:
        r = requests.get(url, headers={"X-API-Key": api_key}, timeout=10)
        r.raise_for_status()
        data = r.json()
        return AccountInfo(
            email=data.get("email"),
            plan=data.get("plan", "free"),
            calls=int(data.get("calls", 0)),
            audio_seconds=float(data.get("audio_seconds", 0.0)),
            trial_started_at=int(data.get("trial_started_at", 0) or 0),
            trial_expires_at=int(data.get("trial_expires_at", 0) or 0),
            subscription_active_until=int(data.get("subscription_active_until", 0) or 0),
            has_access=bool(data.get("has_access", True)),
        )
    except requests.RequestException as e:
        _log.warning("Account info fetch failed: %s", e)
        return None


def upgrade_url(transcribe_endpoint: str, api_key: str) -> str:
    """Resolve the URL the desktop app should open when the user clicks
    Upgrade. Falls back to the static pricing page if the server doesn't
    answer or auth fails — better than a dead button."""
    base = _account_base(transcribe_endpoint)
    fallback = f"{base}/pricing"
    if not api_key:
        return fallback
    try:
        r = requests.get(
            f"{base}/v1/account/upgrade-url",
            headers={"X-API-Key": api_key},
            timeout=8,
        )
        r.raise_for_status()
        return r.json().get("url") or fallback
    except requests.RequestException:
        return fallback
