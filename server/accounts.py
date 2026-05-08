"""Lightweight account database for Synapse Voice.

Email-keyed accounts with auto-generated API keys. Stored in SQLite at
$ACCOUNTS_DB (default /data/accounts.db inside the container).

Designed for self-service onboarding — user enters email in the desktop
app, server returns an API key (creating the account on first request).
"""
from __future__ import annotations

import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("ACCOUNTS_DB", "/data/accounts.db"))


TRIAL_DAYS = 7  # 7-day Pro trial on signup — see v0.3.22 release notes


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                email      TEXT PRIMARY KEY,
                api_key    TEXT NOT NULL UNIQUE,
                plan       TEXT NOT NULL DEFAULT 'free',
                created_at INTEGER NOT NULL,
                last_used  INTEGER
            )
            """
        )
        # v0.3.22: add subscription columns. SQLite doesn't have IF NOT
        # EXISTS for ALTER, so we discover existing columns and add the
        # missing ones — keeps old DBs working.
        cols = {row["name"] for row in c.execute("PRAGMA table_info(accounts)").fetchall()}
        if "trial_started_at" not in cols:
            c.execute("ALTER TABLE accounts ADD COLUMN trial_started_at INTEGER NOT NULL DEFAULT 0")
        if "subscription_active_until" not in cols:
            c.execute("ALTER TABLE accounts ADD COLUMN subscription_active_until INTEGER NOT NULL DEFAULT 0")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                api_key    TEXT NOT NULL,
                ts         INTEGER NOT NULL,
                kind       TEXT NOT NULL,
                duration_s REAL,
                FOREIGN KEY (api_key) REFERENCES accounts(api_key)
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_usage_key ON usage_events(api_key)")


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _new_api_key() -> str:
    return f"sk-svc-{secrets.token_hex(24)}"


class EmailAlreadyRegistered(Exception):
    """Raised when sign-up is called for an email that already has an
    account. The caller must NOT return the existing api_key — that would
    be account takeover by anyone who knows the email address."""


def create_account(email: str) -> dict:
    """Create a new account for `email`. Raises EmailAlreadyRegistered if
    the email is already on file (the caller must direct the user to a
    proper recovery flow rather than echoing the existing key).

    New accounts start a TRIAL_DAYS Pro trial automatically. Returns
    {email, api_key, plan, created_at, trial_started_at, trial_expires_at}.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("invalid email")

    now = int(time.time())
    trial_started_at = now
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM accounts WHERE email = ?", (email,)
        ).fetchone()
        if row:
            raise EmailAlreadyRegistered(email)
        api_key = _new_api_key()
        c.execute(
            """
            INSERT INTO accounts
                (email, api_key, plan, created_at, trial_started_at)
            VALUES (?, ?, 'trial', ?, ?)
            """,
            (email, api_key, now, trial_started_at),
        )
        return {
            "email": email,
            "api_key": api_key,
            "plan": "trial",
            "created_at": now,
            "trial_started_at": trial_started_at,
            "trial_expires_at": trial_started_at + TRIAL_DAYS * 86400,
        }


def email_exists(email: str) -> bool:
    email = email.strip().lower()
    if not email:
        return False
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM accounts WHERE email = ?", (email,)
        ).fetchone()
        return row is not None


def lookup_by_key(api_key: str) -> dict | None:
    if not api_key:
        return None
    with _conn() as c:
        row = c.execute(
            """
            SELECT email, api_key, plan, created_at,
                   trial_started_at, subscription_active_until
            FROM accounts WHERE api_key = ?
            """,
            (api_key,),
        ).fetchone()
        if not row:
            return None
        return {
            "email": row["email"],
            "api_key": row["api_key"],
            "plan": row["plan"],
            "created_at": row["created_at"],
            "trial_started_at": row["trial_started_at"] or 0,
            "subscription_active_until": row["subscription_active_until"] or 0,
        }


def trial_expires_at(account: dict) -> int:
    """Compute trial expiry timestamp from `trial_started_at`. Returns 0
    if the account never started a trial (legacy / `plan='free'` accounts
    pre-v0.3.22)."""
    started = account.get("trial_started_at") or 0
    if not started:
        return 0
    return started + TRIAL_DAYS * 86400


def has_active_access(account: dict) -> bool:
    """True iff the account is allowed to call gated endpoints right now.

    Pro = paid + within their subscription window.
    Trial = trial started + within TRIAL_DAYS.
    Free = no.
    """
    now = int(time.time())
    plan = (account.get("plan") or "free").lower()
    if plan == "pro":
        return (account.get("subscription_active_until") or 0) > now
    if plan == "trial":
        exp = trial_expires_at(account)
        return exp > now
    return False


def set_pro(api_key: str, valid_until_unix: int) -> None:
    """Mark an account as Pro until the given timestamp. Used by the
    /upgrade webhook (Stripe will eventually post here)."""
    with _conn() as c:
        c.execute(
            """
            UPDATE accounts
            SET plan='pro', subscription_active_until=?
            WHERE api_key=?
            """,
            (int(valid_until_unix), api_key),
        )


def record_usage(api_key: str, kind: str, duration_s: float = 0.0) -> None:
    if not api_key:
        return
    now = int(time.time())
    with _conn() as c:
        c.execute(
            "INSERT INTO usage_events (api_key, ts, kind, duration_s) VALUES (?, ?, ?, ?)",
            (api_key, now, kind, duration_s),
        )
        c.execute(
            "UPDATE accounts SET last_used = ? WHERE api_key = ?",
            (now, api_key),
        )


def usage_summary(api_key: str) -> dict:
    """Aggregate usage stats — total calls + total seconds."""
    if not api_key:
        return {"calls": 0, "audio_seconds": 0.0}
    with _conn() as c:
        row = c.execute(
            """
            SELECT COUNT(*) AS calls, COALESCE(SUM(duration_s), 0) AS audio
            FROM usage_events
            WHERE api_key = ?
            """,
            (api_key,),
        ).fetchone()
        return {"calls": row["calls"] or 0, "audio_seconds": row["audio"] or 0.0}
