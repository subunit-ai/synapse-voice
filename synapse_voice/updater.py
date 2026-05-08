"""Auto-update checker — polls GitHub Releases for a newer version.

Network call is best-effort and timeouts quickly so app startup isn't
delayed if GitHub is slow. Result is surfaced to the user via a small
modal dialog ("Update v0.x.y → v0.z.w available — Open release page?").
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import requests

from . import __version__
from .logger import get as _get_logger

_log = _get_logger(__name__)

GITHUB_LATEST = "https://api.github.com/repos/subunit-ai/synapse-voice/releases/latest"


@dataclass
class UpdateInfo:
    current: str
    latest: str
    release_url: str
    body: str
    available: bool


def _parse_version(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("v")
    parts = re.findall(r"\d+", v)
    return tuple(int(p) for p in parts) if parts else (0,)


def check(timeout: float = 5.0) -> Optional[UpdateInfo]:
    """Return UpdateInfo if a newer release is available, else None.

    Returns None on any network/parse error too — silently fails so a
    flaky network never gates app startup.
    """
    try:
        r = requests.get(
            GITHUB_LATEST,
            headers={"Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        tag = data.get("tag_name") or ""
        url = data.get("html_url") or ""
        body = (data.get("body") or "").strip()
    except requests.RequestException as e:
        _log.debug("Update check failed: %s", e)
        return None
    except (KeyError, ValueError) as e:
        _log.debug("Update check parse error: %s", e)
        return None

    if not tag:
        return None
    latest = _parse_version(tag)
    current = _parse_version(__version__)
    available = latest > current
    if available:
        _log.info("Update available: %s → %s", __version__, tag)
    return UpdateInfo(
        current=__version__,
        latest=tag,
        release_url=url,
        body=body,
        available=available,
    )
