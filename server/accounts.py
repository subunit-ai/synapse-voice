"""Lightweight account database for Synapse Voice.

Email-keyed accounts with auto-generated API keys. Stored in SQLite at
$ACCOUNTS_DB (default /data/accounts.db inside the container).

Designed for self-service onboarding — user enters email in the desktop
app, gets a 6-digit verification code by email, the server creates the
account once the code is verified.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("ACCOUNTS_DB", "/data/accounts.db"))


TRIAL_DAYS = 7  # 7-day Pro trial on signup — see v0.3.22 release notes

# v0.5.0: Email-verified signup.  A 6-digit code is mailed to the user;
# they enter it in the desktop app to confirm ownership of the address.
SIGNUP_CODE_TTL = 10 * 60         # 10 minutes
SIGNUP_RESEND_COOLDOWN = 30        # seconds between code-request hits per email
SIGNUP_MAX_ATTEMPTS = 5            # failed verify attempts before lockout


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
        # v0.5.0: Email-verified signup.  We store ONLY a hashed copy of
        # the 6-digit code so a DB leak doesn't trivially reveal pending
        # codes.  Constant-time compare on verify.
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_signups (
                email         TEXT PRIMARY KEY,
                code_hash     TEXT NOT NULL,
                expires_at    INTEGER NOT NULL,
                created_at    INTEGER NOT NULL,
                last_sent_at  INTEGER NOT NULL,
                attempts      INTEGER NOT NULL DEFAULT 0
            )
            """
        )


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


def lookup_by_email(email: str) -> dict | None:
    """Find a legacy per-user account by its email address.

    Used by the JWT-auth path (v0.9.5) to preserve ChromaDB collection
    continuity when a user migrates from X-API-Key → Subunit-Auth login:
    we surface the legacy api_key so /v1/synapse/save keeps writing to
    the SAME hashed collection name instead of splitting history.
    """
    if not email:
        return None
    with _conn() as c:
        row = c.execute(
            """
            SELECT email, api_key, plan, created_at,
                   trial_started_at, subscription_active_until
            FROM accounts WHERE email = ?
            """,
            (email.strip().lower(),),
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


# ── Email-verified signup (v0.5.0) ─────────────────────────────────────


class SignupCodeRateLimited(Exception):
    """Raised when a fresh code is requested before the cooldown elapsed."""

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"retry after {retry_after_seconds}s")
        self.retry_after = retry_after_seconds


class SignupCodeNotFound(Exception):
    """No pending signup for this email (or the row already expired)."""


class SignupCodeExpired(Exception):
    """The pending row exists but the TTL elapsed."""


class SignupCodeWrong(Exception):
    """Code didn't match.  Caller must show a hint and decrement attempts."""

    def __init__(self, attempts_remaining: int) -> None:
        super().__init__(f"{attempts_remaining} attempts remaining")
        self.attempts_remaining = attempts_remaining


class SignupCodeLocked(Exception):
    """Too many wrong attempts on this email.  Forces a fresh request-code."""


def _hash_code(email: str, code: str) -> str:
    """SHA-256 the code with the email as a salt — keeps a leaked DB
    from being usable as a global rainbow table over six-digit numbers."""
    h = hashlib.sha256()
    h.update(email.strip().lower().encode("utf-8"))
    h.update(b"|")
    h.update(code.encode("utf-8"))
    return h.hexdigest()


def request_signup_code(email: str) -> tuple[str, int]:
    """Issue a fresh 6-digit code for `email` and store its hash.

    Returns (code_plaintext, ttl_seconds).  The caller is responsible for
    delivering the code via email — we never log or persist plaintext
    codes here.

    Raises:
        ValueError on malformed email.
        EmailAlreadyRegistered if an account already exists (the user must
            use the existing key, not create a duplicate).
        SignupCodeRateLimited if the cooldown hasn't elapsed since the
            previous send for this email.
    """
    email = email.strip().lower()
    if not email or "@" not in email or len(email) < 5:
        raise ValueError("invalid email")

    now = int(time.time())
    with _conn() as c:
        # Refuse if an account already exists.  We treat this as 409 at
        # the HTTP layer — the user should switch to a recovery flow.
        if c.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
            raise EmailAlreadyRegistered(email)

        existing = c.execute(
            "SELECT last_sent_at FROM pending_signups WHERE email = ?", (email,)
        ).fetchone()
        if existing:
            wait = existing["last_sent_at"] + SIGNUP_RESEND_COOLDOWN - now
            if wait > 0:
                raise SignupCodeRateLimited(wait)

        code = f"{secrets.randbelow(1_000_000):06d}"
        c.execute(
            """
            INSERT INTO pending_signups
                (email, code_hash, expires_at, created_at, last_sent_at, attempts)
            VALUES (?, ?, ?, ?, ?, 0)
            ON CONFLICT(email) DO UPDATE SET
                code_hash    = excluded.code_hash,
                expires_at   = excluded.expires_at,
                last_sent_at = excluded.last_sent_at,
                attempts     = 0
            """,
            (email, _hash_code(email, code), now + SIGNUP_CODE_TTL, now, now),
        )
    return code, SIGNUP_CODE_TTL


def verify_signup_code(email: str, code: str) -> dict:
    """Validate `code` for `email`; on match, create the account and clear
    the pending row.

    Raises:
        ValueError on bad input shape.
        SignupCodeNotFound if no pending row.
        SignupCodeLocked if attempts exhausted.
        SignupCodeExpired if the TTL elapsed.
        SignupCodeWrong if the code is wrong (attempts decremented).

    On success returns the same dict shape as :func:`create_account`.
    """
    email = email.strip().lower()
    code = (code or "").strip()
    if not email or "@" not in email:
        raise ValueError("invalid email")
    if not code.isdigit() or len(code) != 6:
        raise ValueError("invalid code shape")

    now = int(time.time())
    with _conn() as c:
        row = c.execute(
            """
            SELECT code_hash, expires_at, attempts FROM pending_signups
            WHERE email = ?
            """,
            (email,),
        ).fetchone()
        if not row:
            raise SignupCodeNotFound(email)
        if row["attempts"] >= SIGNUP_MAX_ATTEMPTS:
            # Lock state — the user must hit /request-code again, which
            # resets attempts to 0 via the ON CONFLICT update above.
            raise SignupCodeLocked()
        if row["expires_at"] < now:
            c.execute("DELETE FROM pending_signups WHERE email = ?", (email,))
            raise SignupCodeExpired()

        ok = hmac.compare_digest(_hash_code(email, code), row["code_hash"])
        if not ok:
            new_attempts = row["attempts"] + 1
            c.execute(
                "UPDATE pending_signups SET attempts = ? WHERE email = ?",
                (new_attempts, email),
            )
            remaining = max(0, SIGNUP_MAX_ATTEMPTS - new_attempts)
            raise SignupCodeWrong(remaining)

        # Code matches → create the account and discard the pending row.
        if c.execute("SELECT 1 FROM accounts WHERE email = ?", (email,)).fetchone():
            # Race: an account was created between request_code and now.
            # Clean up but don't echo the api_key.
            c.execute("DELETE FROM pending_signups WHERE email = ?", (email,))
            raise EmailAlreadyRegistered(email)

        api_key = _new_api_key()
        trial_started_at = now
        c.execute(
            """
            INSERT INTO accounts
                (email, api_key, plan, created_at, trial_started_at)
            VALUES (?, ?, 'trial', ?, ?)
            """,
            (email, api_key, now, trial_started_at),
        )
        c.execute("DELETE FROM pending_signups WHERE email = ?", (email,))
        return {
            "email": email,
            "api_key": api_key,
            "plan": "trial",
            "created_at": now,
            "trial_started_at": trial_started_at,
            "trial_expires_at": trial_started_at + TRIAL_DAYS * 86400,
        }


def purge_expired_pending_signups() -> int:
    """Delete all pending rows whose TTL elapsed.  Returns the count.
    Cheap to run on every request-code call."""
    now = int(time.time())
    with _conn() as c:
        cur = c.execute(
            "DELETE FROM pending_signups WHERE expires_at < ?", (now,)
        )
        return cur.rowcount or 0
