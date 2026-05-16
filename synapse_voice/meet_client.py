"""HTTP client for the Subunit Meet endpoints on transcribe.subunit.ai.

Used by the Sonar-Desktop Meeting-Start modal to:
  - create a meeting (POST /v1/meetings)
  - list live participants (GET /v1/meetings/<code>/participants)
  - start / end the meeting (POST /v1/meetings/<code>/start|end)

Best-effort: every call is wrapped in try/except so the modal stays
responsive even when the network blips.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import requests

from .logger import get as _get_logger

_log = _get_logger(__name__)


@dataclass
class Meeting:
    code: str
    title: str
    host_token: str
    share_url: str

    @classmethod
    def from_dict(cls, d: dict) -> "Meeting":
        return cls(
            code=str(d.get("code", "")),
            title=str(d.get("title", "")),
            host_token=str(d.get("host_token", "")),
            share_url=str(d.get("share_url", "")),
        )


@dataclass
class Participant:
    name: str
    joined_at_relative: str
    source: str
    token: str = ""        # v0.9.4: needed for host approve/reject
    pending: bool = False  # v0.9.4: true while waiting in the Warteraum

    @classmethod
    def from_dict(cls, d: dict) -> "Participant":
        return cls(
            name=str(d.get("name", "")),
            joined_at_relative=str(d.get("joined_at_relative", "")),
            source=str(d.get("source", "")),
            token=str(d.get("token", "")),
            pending=bool(d.get("pending", False)),
        )


def _base_url(transcribe_endpoint: str) -> str:
    base = transcribe_endpoint.rstrip("/")
    for marker in ("/v1/transcribe", "/v1"):
        if base.endswith(marker):
            base = base[: -len(marker)]
            break
    return base.rstrip("/")


def create_meeting(
    transcribe_endpoint: str,
    api_key: str,
    *,
    host_name: str,
    host_email: str | None = None,
    title: str | None = None,
    timeout: float = 12.0,
) -> Optional[Meeting]:
    if not transcribe_endpoint or not api_key:
        return None
    url = f"{_base_url(transcribe_endpoint)}/v1/meetings"
    try:
        r = requests.post(
            url,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            json={
                "host_name": host_name,
                "host_email": host_email,
                "title": title,
            },
            timeout=timeout,
        )
        if r.status_code >= 400:
            _log.warning("Meet/create HTTP %s: %s", r.status_code, r.text[:200])
            return None
        return Meeting.from_dict(r.json())
    except requests.RequestException as e:
        _log.warning("Meet/create failed: %s", e)
        return None


def host_self_join(
    transcribe_endpoint: str,
    code: str,
    *,
    host_name: str,
    host_email: str | None,
    timeout: float = 6.0,
) -> Optional[str]:
    """Self-register the host as a participant so the audio-stream WS
    endpoint accepts their mic feed. Returns the join_token on success.

    The post-pipeline treats this participant exactly like guests —
    speaker label = host_name, recap email goes to host_email if given.
    Distinguished only by `source="host"` so the UI can render them
    differently in the check-in list."""
    url = f"{_base_url(transcribe_endpoint)}/v1/meetings/{code}/join"
    payload = {
        "name": host_name or "Host",
        "email": host_email or "host@subunit.local",  # required by API; harmless dummy
        "source": "host",
    }
    try:
        r = requests.post(url, json=payload, timeout=timeout)
        if r.status_code >= 400:
            _log.warning("Meet/host-join HTTP %s: %s", r.status_code, r.text[:200])
            return None
        return r.json().get("join_token")
    except requests.RequestException as e:
        _log.warning("Meet/host-join failed: %s", e)
        return None


def list_participants(
    transcribe_endpoint: str,
    code: str,
    host_token: str,
    timeout: float = 6.0,
) -> Optional[list[Participant]]:
    if not code or not host_token:
        return None
    url = f"{_base_url(transcribe_endpoint)}/v1/meetings/{code}/participants"
    try:
        r = requests.get(url, params={"host_token": host_token}, timeout=timeout)
        if r.status_code >= 400:
            return None
        body = r.json()
        return [Participant.from_dict(p) for p in body.get("participants") or []]
    except requests.RequestException:
        return None


def approve_participant(
    transcribe_endpoint: str, code: str, participant_token: str, host_token: str,
    timeout: float = 6.0,
) -> bool:
    url = f"{_base_url(transcribe_endpoint)}/v1/meetings/{code}/participants/{participant_token}/approve"
    try:
        r = requests.post(url, params={"host_token": host_token}, timeout=timeout)
        return r.ok
    except requests.RequestException:
        return False


def reject_participant(
    transcribe_endpoint: str, code: str, participant_token: str, host_token: str,
    timeout: float = 6.0,
) -> bool:
    url = f"{_base_url(transcribe_endpoint)}/v1/meetings/{code}/participants/{participant_token}/reject"
    try:
        r = requests.post(url, params={"host_token": host_token}, timeout=timeout)
        return r.ok
    except requests.RequestException:
        return False


def start_meeting(transcribe_endpoint: str, code: str, host_token: str, timeout: float = 6.0) -> bool:
    url = f"{_base_url(transcribe_endpoint)}/v1/meetings/{code}/start"
    try:
        r = requests.post(url, params={"host_token": host_token}, timeout=timeout)
        return r.ok
    except requests.RequestException:
        return False


def end_meeting(transcribe_endpoint: str, code: str, host_token: str, timeout: float = 6.0) -> bool:
    url = f"{_base_url(transcribe_endpoint)}/v1/meetings/{code}/end"
    try:
        r = requests.post(url, params={"host_token": host_token}, timeout=timeout)
        return r.ok
    except requests.RequestException:
        return False
