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


def get_or_create(email: str) -> dict:
    """Look up an account by email; create it if it doesn't exist.

    Returns {email, api_key, plan, created_at, is_new}.
    """
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("invalid email")

    now = int(time.time())
    with _conn() as c:
        row = c.execute(
            "SELECT email, api_key, plan, created_at FROM accounts WHERE email = ?",
            (email,),
        ).fetchone()
        if row:
            return {
                "email": row["email"],
                "api_key": row["api_key"],
                "plan": row["plan"],
                "created_at": row["created_at"],
                "is_new": False,
            }
        api_key = _new_api_key()
        c.execute(
            """
            INSERT INTO accounts (email, api_key, plan, created_at)
            VALUES (?, ?, 'free', ?)
            """,
            (email, api_key, now),
        )
        return {
            "email": email,
            "api_key": api_key,
            "plan": "free",
            "created_at": now,
            "is_new": True,
        }


def lookup_by_key(api_key: str) -> dict | None:
    if not api_key:
        return None
    with _conn() as c:
        row = c.execute(
            "SELECT email, api_key, plan, created_at FROM accounts WHERE api_key = ?",
            (api_key,),
        ).fetchone()
        if not row:
            return None
        return {
            "email": row["email"],
            "api_key": row["api_key"],
            "plan": row["plan"],
            "created_at": row["created_at"],
        }


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
