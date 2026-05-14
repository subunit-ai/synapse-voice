"""Thin HTTP client for the local Subunit Bridge daemon (localhost:7842).

Sonar uses this to push Tasks / Decisions / Memory entries into the user's
Subunit workspace via the locally-running Bridge, which queues them in an
SQLite outbox and forwards to ``api.subunit.ai`` in the background.

The Bridge is optional — if it's not installed/running, all calls fail
quickly and the caller falls back to clipboard/log behavior.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

_log = logging.getLogger(__name__)

DEFAULT_BASE = "http://127.0.0.1:7842"
TIMEOUT_SECONDS = 3.0


class BridgeError(Exception):
    """Raised when the Bridge call fails (unreachable, auth, or HTTP error)."""


class BridgeClient:
    def __init__(self, base_url: str = DEFAULT_BASE) -> None:
        self._base = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        try:
            data = self._request("GET", "/health")
            return bool(data.get("ok"))
        except BridgeError:
            return False

    def is_paired(self) -> bool:
        try:
            data = self._request("GET", "/auth/status")
            return bool(data.get("paired"))
        except BridgeError:
            return False

    def status(self) -> dict:
        try:
            return self._request("GET", "/auth/status")
        except BridgeError:
            return {"paired": False, "available": False}

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def create_task(self, title: str, *, priority: str | None = None, metadata: dict | None = None) -> dict:
        body = {"title": title.strip()}
        meta: dict[str, Any] = {}
        if priority:
            meta["priority"] = priority
        if metadata:
            meta.update(metadata)
        if meta:
            body["metadata"] = meta
        return self._request("POST", "/tasks", body)

    def create_decision(self, title: str, *, body: str | None = None, source: str = "sonar", metadata: dict | None = None) -> dict:
        payload: dict[str, Any] = {"title": title.strip(), "source": source}
        if body:
            payload["body"] = body
        if metadata:
            payload["metadata"] = metadata
        return self._request("POST", "/decisions", payload)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = self._base + path
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
                raw = r.read()
                if not raw:
                    return {}
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")
            except Exception:
                detail = ""
            raise BridgeError(f"bridge_http_{e.code}: {detail[:300]}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise BridgeError(f"bridge_unreachable: {e}") from e
        except json.JSONDecodeError as e:
            raise BridgeError(f"bridge_bad_response: {e}") from e
