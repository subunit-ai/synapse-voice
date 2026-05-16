"""Verify Subunit-Auth JWTs (RS256) on the transcribe-server.

The Sonar Desktop login flow (browser → auth.subunit.ai) deposits a
short-lived access_token in the user's config. Sonar then sends it as
``Authorization: Bearer <jwt>`` on every cloud transcribe call.

This module:
    1. Fetches the JWKS once at module import and caches in memory.
    2. Re-fetches if a token references a kid we don't know yet
       (handles RSA key rotation without restart).
    3. Verifies signature + issuer + audience + exp.

Returns a normalised dict ``{"sub": user_id, "email": "...", "ws": ws_id,
"plan": "...", "is_operator": bool}`` or raises ValueError.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import jwt
import requests
from jwt import PyJWKClient

LOG = logging.getLogger(__name__)

AUTH_ISSUER = "https://auth.subunit.ai"
JWKS_URL = f"{AUTH_ISSUER}/.well-known/jwks.json"
# Audiences we accept. Sonar Desktop sends `aud=sonar-desktop`; the
# first-party API tokens use `aud=first-party`. Both are legitimate
# Subunit clients, so we trust either.
ACCEPTED_AUDIENCES = ("sonar-desktop", "first-party")
# Required scope claim — every JWT the transcribe-server accepts MUST
# carry "transcribe" in its space-separated scope. Tokens minted for
# other surfaces (e.g. /me-only scopes) won't be usable here even if
# the audience matches.
REQUIRED_SCOPE = "transcribe"

# PyJWKClient caches signing keys + handles the kid lookup. Lifetime is
# managed by the underlying urllib3 connection pool — we keep ONE
# instance for the process to share the cache across requests.
#
# A real User-Agent is required: auth.subunit.ai lives behind Cloudflare
# and the default urllib "Python-urllib/3.x" UA gets a blanket 403 from
# the WAF. Sending a recognisable string passes through.
_jwks_client: PyJWKClient = PyJWKClient(
    JWKS_URL,
    cache_keys=True,
    lifespan=3600,
    headers={"User-Agent": "transcribe.subunit.ai jwt-verify/1.0"},
)

# Plain in-memory cache of the full JWKS doc for the `iss` check below.
_jwks_doc: dict = {}
_jwks_lock = threading.Lock()


def _refresh_jwks_doc() -> None:
    global _jwks_doc
    try:
        r = requests.get(
            JWKS_URL,
            timeout=10,
            headers={"User-Agent": "transcribe.subunit.ai jwt-verify/1.0"},
        )
        r.raise_for_status()
        with _jwks_lock:
            _jwks_doc = r.json()
    except Exception as e:
        LOG.warning("jwt_verify: jwks fetch failed: %s", e)


def verify_subunit_jwt(token: str) -> dict:
    """Decode + verify the JWT. Returns the claims dict, or raises ValueError.

    Caller wraps the ValueError in an HTTP 401.
    """
    if not token:
        raise ValueError("empty token")

    try:
        signing_key = _jwks_client.get_signing_key_from_jwt(token).key
    except Exception as e:
        # Could be kid not in cache — force a refresh and retry once.
        LOG.info("jwt_verify: jwks miss, refreshing: %s", e)
        _refresh_jwks_doc()
        try:
            signing_key = _jwks_client.get_signing_key_from_jwt(token).key
        except Exception as e2:
            raise ValueError(f"jwks lookup failed: {e2}") from e2

    try:
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=AUTH_ISSUER,
            # PyJWT requires explicit aud check; we accept any of our clients.
            audience=list(ACCEPTED_AUDIENCES),
            options={"verify_iat": False},  # iat isn't always set by Bun.jose
        )
    except jwt.InvalidTokenError as e:
        raise ValueError(f"invalid jwt: {e}") from e

    # Operators get a pass on email_verified + scope checks. They use
    # short-lived ops tokens that may carry a wider scope set, and we
    # want to be able to mint admin tokens before the user has clicked
    # the verification email.
    is_operator = bool(claims.get("op"))

    if not is_operator:
        # 2026-05-16 (Codex P1): refuse unverified accounts on cloud
        # endpoints. The browser flow only mints tokens for verified
        # accounts today, but the JWT itself is the source of truth —
        # don't trust that invariant downstream.
        if not bool(claims.get("email_verified")):
            raise ValueError("email_not_verified")

        # 2026-05-16 (Codex P1): require the explicit "transcribe" scope
        # so first-party tokens minted for other surfaces (e.g. SNI
        # workspace-read tokens) can't be replayed against /v1/transcribe.
        scopes = set((claims.get("scope") or "").split())
        if REQUIRED_SCOPE not in scopes:
            raise ValueError(f"missing scope: {REQUIRED_SCOPE}")

    return {
        "sub":         claims.get("sub"),
        "email":       claims.get("email", ""),
        "email_verified": bool(claims.get("email_verified")),
        "ws":          claims.get("ws"),
        "wss":         claims.get("wss") or [],
        "role":        claims.get("role"),
        "scope":       claims.get("scope", ""),
        "is_operator": is_operator,
        "aud":         claims.get("aud"),
        "exp":         claims.get("exp"),
    }


# Tier-sync cache: maps token → (tier_dict, fetched_at_unix). 60s TTL is
# short enough that an operator-driven Pro-upgrade in the admin panel
# propagates within a minute, long enough that we don't hammer the auth
# server on every transcribe call.
_TIER_CACHE: dict[str, tuple[dict, float]] = {}
_TIER_CACHE_TTL = 60.0
# Tiers that count as "active access" for gated endpoints.
_ACTIVE_TIERS = ("pro", "enterprise", "pilot", "ops")


def fetch_workspace_tier(access_token: str) -> dict | None:
    """Query auth.subunit.ai/me/workspace/active for the JWT caller's
    actual workspace tier. Returns ``{tier, kind, slug, id, retention_days}``
    or ``None`` on lookup failure (caller should fall back to default).

    Cached for 60s per token — admin tier changes propagate in <=1min,
    transcribe stays cheap on the auth server.
    """
    now = time.time()
    cached = _TIER_CACHE.get(access_token)
    if cached and (now - cached[1]) < _TIER_CACHE_TTL:
        return cached[0]

    try:
        r = requests.get(
            f"{AUTH_ISSUER}/me/workspace/active",
            timeout=8,
            headers={
                "Authorization": f"Bearer {access_token}",
                "User-Agent": "transcribe.subunit.ai jwt-verify/1.0",
            },
        )
        if r.status_code == 404:
            # User has no workspace yet (shouldn't happen for signed-up
            # accounts, but don't crash). Treat as free.
            data = {"tier": "free", "kind": "personal", "slug": "", "id": "", "retention_days": 90}
        elif r.status_code == 200:
            payload = r.json()
            data = payload.get("workspace") or {}
        else:
            LOG.warning("fetch_workspace_tier: HTTP %d", r.status_code)
            return None
    except Exception as e:
        LOG.warning("fetch_workspace_tier: %s", e)
        return None

    _TIER_CACHE[access_token] = (data, now)
    # Periodic GC — don't let the cache grow forever.
    if len(_TIER_CACHE) > 1024:
        cutoff = now - _TIER_CACHE_TTL * 2
        for k in [k for k, (_, t) in _TIER_CACHE.items() if t < cutoff]:
            del _TIER_CACHE[k]
    return data


def tier_has_active_access(tier: str | None) -> bool:
    """Map an auth-server tier label to the binary 'allowed to call gated
    endpoints' decision."""
    return (tier or "").lower() in _ACTIVE_TIERS
