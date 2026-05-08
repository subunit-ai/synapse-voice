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
