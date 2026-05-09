"""Outbound email via Resend (resend.com).

Used by /v1/account/request-code to deliver a 6-digit verification code
to the user's mailbox before the account is created.

Configuration:
  RESEND_API_KEY   — required. Picked up by the Docker container from
                     the orchestrator-level env or a mounted secret.
  RESEND_FROM      — sender address. Default "Sonar <hello@subunit.ai>".
                     The domain must be verified on Resend.
"""
from __future__ import annotations

import logging
import os
from typing import Final

import requests

logger = logging.getLogger(__name__)

RESEND_API_KEY: Final[str] = os.environ.get("RESEND_API_KEY", "").strip()
RESEND_FROM: Final[str] = os.environ.get(
    "RESEND_FROM", "Sonar <hello@subunit.ai>"
).strip()
RESEND_ENDPOINT: Final[str] = "https://api.resend.com/emails"


class EmailDeliveryError(RuntimeError):
    """Raised when Resend rejects a send.  The API endpoint maps this to
    HTTP 502 so the caller can show the user a "couldn't send — try
    again" hint without leaking provider internals."""


def _verification_subject() -> str:
    return "Sonar — Bestätigungscode"


def _verification_body(code: str) -> tuple[str, str]:
    """Returns (text, html).  Plain-text is the source of truth so the
    code is readable even if the HTML version gets stripped/blocked."""
    text = (
        f"Dein Bestätigungscode für Sonar:\n\n"
        f"    {code}\n\n"
        f"Gib diesen Code in der Sonar-App ein, um dein Konto zu aktivieren.\n"
        f"Der Code ist 10 Minuten gültig.\n\n"
        f"Wenn du diesen Code nicht angefordert hast, kannst du diese Mail "
        f"einfach ignorieren — es wurde noch nichts angelegt.\n\n"
        f"— Subunit | https://subunit.ai/sonar/"
    )
    html = f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;
                    background:#f6f8fa;padding:32px 16px;color:#0f172a;">
          <div style="max-width:480px;margin:0 auto;background:#fff;border-radius:18px;
                      padding:32px 28px;box-shadow:0 8px 32px rgba(15,23,42,0.06);">
            <div style="font-size:13px;letter-spacing:0.16em;text-transform:uppercase;
                        color:#06b6d4;font-weight:700;">Sonar by Subunit</div>
            <h1 style="margin:8px 0 18px 0;font-size:24px;line-height:1.3;font-weight:800;
                       color:#0f172a;">Dein Bestätigungscode</h1>
            <p style="margin:0 0 20px 0;font-size:15px;line-height:1.55;color:#334155;">
              Gib diesen Code in der Sonar-App ein, um dein Konto zu aktivieren:
            </p>
            <div style="font-family:'SF Mono','Menlo','Consolas',monospace;font-size:36px;
                        letter-spacing:0.32em;text-align:center;padding:18px 16px;
                        background:#0f172a;color:#22d3ee;border-radius:12px;font-weight:700;">
              {code}
            </div>
            <p style="margin:20px 0 0 0;font-size:13px;line-height:1.6;color:#64748b;">
              Der Code ist 10 Minuten gültig. Wenn du diesen Code nicht angefordert hast,
              kannst du diese Mail einfach ignorieren — es wurde noch nichts angelegt.
            </p>
            <p style="margin:24px 0 0 0;font-size:12px;color:#94a3b8;">
              — <a href="https://subunit.ai/sonar/" style="color:#06b6d4;text-decoration:none;">
                subunit.ai/sonar
              </a>
            </p>
          </div>
        </div>
    """.strip()
    return text, html


def send_verification_code(email: str, code: str) -> None:
    """Deliver the 6-digit code to `email`.

    Raises EmailDeliveryError if Resend rejects the request.  Network
    errors propagate as the same exception type so the caller can map
    them to one HTTP status.
    """
    if not RESEND_API_KEY:
        # Loud failure rather than silent drop — the user MUST get the
        # code, otherwise they're stuck.
        raise EmailDeliveryError("RESEND_API_KEY is not configured on the server")

    text, html = _verification_body(code)
    payload = {
        "from": RESEND_FROM,
        "to": [email],
        "subject": _verification_subject(),
        "text": text,
        "html": html,
    }
    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }
    try:
        r = requests.post(RESEND_ENDPOINT, json=payload, headers=headers, timeout=10)
    except requests.RequestException as e:
        logger.warning("resend network error for %s: %s", email, e)
        raise EmailDeliveryError(f"network error: {e}") from e
    if r.status_code >= 400:
        # Don't log the API key, but the response body usually has the
        # reason (e.g. unverified domain, bad recipient) — useful for ops.
        logger.warning("resend rejected %s: %s %s", email, r.status_code, r.text[:200])
        raise EmailDeliveryError(f"resend rejected ({r.status_code})")
