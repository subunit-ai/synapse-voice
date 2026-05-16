"""Browser-based login against auth.subunit.ai.

Pattern (same as rclone / gcloud / GitHub CLI):
    1. Pick a random local port and bind a one-shot HTTP listener on
       ``127.0.0.1:<port>``.
    2. Open the user's default browser at
       ``https://auth.subunit.ai/sonar-login?state=<random>&port=<port>``.
    3. The user logs in (or signs up) in the browser. On success the
       Subunit page redirects the browser to
       ``http://127.0.0.1:<port>/callback?state=<X>&access_token=<Y>&refresh_token=<Z>&workspace_id=<W>``.
    4. The local listener captures the query string, verifies the state,
       returns a "you can close this tab" page and shuts down.
    5. Tokens are persisted (encrypted) in the user's config and used as
       Bearer credentials for cloud transcription calls.

Threading model:
    The HTTP server runs in a daemon thread so the caller (typically the
    Settings dialog or an explicit "Login" action) can join on a result
    queue with a timeout. If the user closes the browser without
    completing, the join times out and the listener thread is told to
    stop on its next iteration.

Security:
    - ``state`` is 32 bytes of secrets.token_urlsafe — opaque, never
      sent over the wire except to auth.subunit.ai and back. Sonar
      verifies the state matches before accepting tokens, preventing
      a malicious local actor from injecting tokens via the listener.
    - The listener binds 127.0.0.1 only — never accessible to other
      machines on the LAN.
    - Only ``/callback`` is honored; any other path returns 404.
"""
from __future__ import annotations

import http.server
import json
import logging
import secrets
import socket
import threading
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from typing import Optional, Tuple

from .logger import get as _get_logger

LOG = _get_logger(__name__)

AUTH_BASE_URL = "https://auth.subunit.ai"
# 5-minute timeout — that's long enough to read an email-verification code
# but short enough that an abandoned login attempt cleans itself up.
LOGIN_TIMEOUT_SECONDS = 300


@dataclass(frozen=True)
class SubunitTokens:
    access_token: str
    refresh_token: str
    expires_in: int          # seconds until access_token expires
    workspace_id: Optional[str]
    issued_at: float         # monotonic-ish; see refresh_if_needed below

    def expires_at(self) -> float:
        return self.issued_at + max(self.expires_in - 30, 30)

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at()


def _pick_free_port() -> int:
    """Bind ephemeral port, immediately close, return the port number.

    Race-safe enough for this flow — the moment we get a port back we
    re-bind in the HTTP server, with the same machine and same process,
    long before any other listener could grab it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """One-shot HTTP handler that resolves the parent flow's Event."""

    # These are set by the parent _LocalCallbackServer instance.
    expected_state: str = ""
    result_box: list = []   # length-1 list used as a mailbox
    done_event: threading.Event = None  # type: ignore

    def log_message(self, format: str, *args) -> None:  # quiet stdout
        LOG.debug("callback-server: " + (format % args))

    def do_GET(self) -> None:  # noqa: N802 — std-lib name
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_error(404, "Not Found")
            return

        params = dict(urllib.parse.parse_qsl(parsed.query))
        if params.get("state") != self.expected_state:
            self._respond_error(
                "Authentifizierung fehlgeschlagen: state mismatch. "
                "Bitte Sonar neu starten und nochmal versuchen."
            )
            self.result_box.append({"error": "state_mismatch"})
            self.done_event.set()
            return

        access = params.get("access_token")
        refresh = params.get("refresh_token")
        expires_in_raw = params.get("expires_in") or "0"
        workspace_id = params.get("workspace_id") or None

        if not access or not refresh:
            self._respond_error(
                "Authentifizierung fehlgeschlagen: Tokens fehlen in der Antwort."
            )
            self.result_box.append({"error": "missing_tokens"})
            self.done_event.set()
            return

        try:
            expires_in = int(expires_in_raw)
        except ValueError:
            expires_in = 900

        # Success — push tokens to the mailbox.
        self.result_box.append({
            "access_token": access,
            "refresh_token": refresh,
            "expires_in": expires_in,
            "workspace_id": workspace_id,
        })
        self._respond_success()
        self.done_event.set()

    # ── Response helpers ──────────────────────────────────────────────

    def _respond_success(self) -> None:
        body = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Sonar verbunden</title>"
            "<style>body{font-family:system-ui,sans-serif;background:#050b1a;color:#e6edf6;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}"
            ".card{text-align:center;max-width:420px;padding:32px 28px;"
            "background:#0a1628;border:1px solid #1d2c44;border-radius:16px}"
            "h1{font-size:22px;margin:0 0 8px}p{color:#8a9bb1;line-height:1.5;font-size:14px;margin:0}"
            ".dot{display:inline-block;width:10px;height:10px;border-radius:50%;background:#06b6d4;"
            "box-shadow:0 0 16px #06b6d4;margin-right:8px;vertical-align:middle}"
            "</style></head><body><main class='card'>"
            "<div><span class='dot'></span><strong>SUBUNIT</strong></div>"
            "<h1 style='margin-top:18px'>Sonar ist verbunden ✓</h1>"
            "<p>Du kannst dieses Browserfenster schliessen und zu Sonar zurueckkehren.</p>"
            "</main></body></html>"
        )
        encoded = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _respond_error(self, msg: str) -> None:
        body = (
            f"<!doctype html><html><head><meta charset='utf-8'><title>Sonar — Fehler</title>"
            "<style>body{font-family:system-ui,sans-serif;background:#050b1a;color:#e6edf6;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}"
            ".card{text-align:center;max-width:420px;padding:32px 28px;background:#0a1628;"
            "border:1px solid #ef4444;border-radius:16px}h1{font-size:22px;color:#ef4444;margin:0 0 8px}"
            "p{color:#8a9bb1;line-height:1.5}</style></head><body><main class='card'>"
            f"<h1>Verbindung fehlgeschlagen</h1><p>{msg}</p></main></body></html>"
        )
        encoded = body.encode("utf-8")
        self.send_response(400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def login_interactive(timeout: int = LOGIN_TIMEOUT_SECONDS) -> Optional[SubunitTokens]:
    """Run the browser flow end-to-end. Returns tokens or None on
    failure / cancellation. Blocks for up to ``timeout`` seconds."""
    port = _pick_free_port()
    state = secrets.token_urlsafe(32)
    result_box: list = []
    done_event = threading.Event()

    handler_cls = type(
        "_BoundHandler",
        (_CallbackHandler,),
        {
            "expected_state": state,
            "result_box": result_box,
            "done_event": done_event,
        },
    )

    server = http.server.HTTPServer(("127.0.0.1", port), handler_cls)
    server.timeout = 1  # short loop so we can poll done_event

    def serve() -> None:
        # handle_request is one-shot; loop until done_event is set or we time out.
        while not done_event.is_set():
            server.handle_request()

    server_thread = threading.Thread(target=serve, name="subunit-auth-cb", daemon=True)
    server_thread.start()

    login_url = (
        f"{AUTH_BASE_URL}/sonar-login"
        f"?state={urllib.parse.quote(state)}"
        f"&port={port}"
    )
    LOG.info("subunit_auth: opening %s", login_url)
    try:
        webbrowser.open(login_url, new=2)
    except Exception as e:
        LOG.warning("subunit_auth: webbrowser.open raised %s — user must open URL manually", e)

    # Wait for the callback OR the timeout.
    if not done_event.wait(timeout=timeout):
        LOG.info("subunit_auth: login timed out after %ds", timeout)
        try:
            server.server_close()
        except Exception:
            pass
        return None

    try:
        server.server_close()
    except Exception:
        pass

    if not result_box or "error" in result_box[0]:
        LOG.info("subunit_auth: login failed (%s)", result_box[0].get("error") if result_box else "no-result")
        return None

    r = result_box[0]
    return SubunitTokens(
        access_token=r["access_token"],
        refresh_token=r["refresh_token"],
        expires_in=r["expires_in"],
        workspace_id=r["workspace_id"],
        issued_at=time.time(),
    )


def refresh_tokens(refresh_token: str) -> Optional[SubunitTokens]:
    """Trade a refresh_token for a fresh access_token + new refresh_token.

    Returns None on any failure (caller should then prompt re-login).
    """
    import urllib.request

    body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
    req = urllib.request.Request(
        f"{AUTH_BASE_URL}/refresh",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "Sonar Desktop"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        LOG.warning("subunit_auth: refresh failed: %s", e)
        return None

    access = payload.get("access_token")
    refresh = payload.get("refresh_token") or refresh_token  # /refresh returns new one
    expires_in = int(payload.get("expires_in") or 900)
    workspace_id = (payload.get("active_workspace_id")
                    or payload.get("workspace_id")
                    or None)
    if not access:
        return None
    return SubunitTokens(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        workspace_id=workspace_id,
        issued_at=time.time(),
    )


def refresh_if_needed(config) -> bool:
    """JIT-refresh the access_token in ``config`` if it's about to expire.

    Returns True if the config now holds a usable (fresh or still-valid)
    access_token, False if there's no token at all OR the refresh failed
    (caller should prompt the user to re-login).

    Persists the refreshed tokens via ``config.save()`` so subsequent
    runs / instances see them. Idempotent — safe to call on every
    transcribe.
    """
    access = getattr(config, "subunit_access_token", "") or ""
    refresh = getattr(config, "subunit_refresh_token", "") or ""
    if not access and not refresh:
        return False

    issued_at = float(getattr(config, "subunit_token_issued_at", 0) or 0)
    expires_in = int(getattr(config, "subunit_token_expires_in", 0) or 0)

    # 30-second safety margin so a request that starts now doesn't get
    # mid-call expiry.
    if access and issued_at and expires_in:
        expires_at = issued_at + max(expires_in - 30, 30)
        if time.time() < expires_at:
            return True  # still valid

    if not refresh:
        # Token expired and no refresh_token to recover from — caller
        # must re-login.
        LOG.info("subunit_auth: access_token expired and no refresh_token")
        return False

    tokens = refresh_tokens(refresh)
    if not tokens:
        LOG.info("subunit_auth: refresh_tokens returned None — re-login required")
        # Wipe the dead tokens so the UI knows we're logged out.
        config.subunit_access_token = ""
        config.subunit_refresh_token = ""
        config.subunit_token_issued_at = 0.0
        config.subunit_token_expires_in = 0
        try:
            config.save()
        except Exception as e:
            LOG.warning("subunit_auth: config.save after wipe failed: %s", e)
        return False

    config.subunit_access_token = tokens.access_token
    config.subunit_refresh_token = tokens.refresh_token
    config.subunit_token_issued_at = tokens.issued_at
    config.subunit_token_expires_in = tokens.expires_in
    if tokens.workspace_id:
        config.subunit_workspace_id = tokens.workspace_id
    try:
        config.save()
    except Exception as e:
        LOG.warning("subunit_auth: config.save after refresh failed: %s", e)
    LOG.info("subunit_auth: access_token refreshed (expires_in=%ds)", tokens.expires_in)
    return True


def fetch_me(access_token: str) -> Optional[dict]:
    """Return the /me payload for diagnostics / settings display."""
    import urllib.request

    req = urllib.request.Request(
        f"{AUTH_BASE_URL}/me",
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "Sonar Desktop",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        LOG.debug("subunit_auth: /me failed: %s", e)
        return None
