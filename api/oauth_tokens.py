"""Stateless signed credentials for the OAuth surface: client ids, authorization codes,
access tokens, and owner passphrase hashes.

Kept free of any api.* import so `_openbrain_api._require_tool_auth` can verify an access
token without a circular import through api.oauth.

Every blob is `<prefix><b64url(json)>.<hmac-sha256>` and carries a `kind` so a code cannot
be replayed as a token or a client id as a code. The signing secret is
OPENBRAIN_OAUTH_SIGNING_SECRET, falling back to OPENBRAIN_TOOL_ACCESS_TOKEN. There is no
built-in default: with neither set, signing raises and the OAuth surface returns 503. The
previous module defaulted to the literal string "openbrain-oauth-secret", which would have
let anyone mint owner codes against a misconfigured deploy.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

CLIENT_PREFIX = "obc_"
TOKEN_PREFIX = "obt_"

ACCESS_TOKEN_TTL = 90 * 24 * 3600   # matches the previous static-bearer expiry
CODE_TTL = 300

PBKDF2_ITERATIONS = 200_000


class OAuthNotConfigured(RuntimeError):
    """No signing secret in the environment — refuse rather than sign with a default."""


def signing_secret() -> bytes:
    raw = os.getenv("OPENBRAIN_OAUTH_SIGNING_SECRET") or os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN")
    if not raw or not raw.strip():
        raise OAuthNotConfigured(
            "set OPENBRAIN_OAUTH_SIGNING_SECRET (or OPENBRAIN_TOOL_ACCESS_TOKEN)")
    return raw.strip().encode()


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * ((4 - len(text) % 4) % 4))


def sign(kind: str, payload: dict[str, Any], prefix: str = "") -> str:
    body = _b64e(json.dumps({**payload, "kind": kind}, separators=(",", ":")).encode())
    sig = hmac.new(signing_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{prefix}{body}.{sig}"


def verify(kind: str, blob: str | None, prefix: str = "") -> dict[str, Any] | None:
    """Decode a signed blob of the given kind. None on any defect: wrong prefix, bad
    signature, wrong kind, expired, or unparseable. Never raises on malformed input."""
    if not blob or not blob.startswith(prefix):
        return None
    try:
        body, sig = blob[len(prefix):].rsplit(".", 1)
        expected = hmac.new(signing_secret(), body.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_b64d(body).decode())
    except OAuthNotConfigured:
        raise
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("kind") != kind:
        return None
    exp = data.get("exp")
    if exp is not None and exp < time.time():
        return None
    return data


# ── access tokens ─────────────────────────────────────────────────────────────

def issue_access_token(owner: str, client_id: str, ttl: int = ACCESS_TOKEN_TTL) -> str:
    now = int(time.time())
    return sign("at", {"owner": owner, "client_id": client_id, "iat": now, "exp": now + ttl},
                prefix=TOKEN_PREFIX)


def verify_access_token(candidate: str | None) -> str | None:
    """Owner the token was issued to, or None. Static bearers (no prefix) are not ours."""
    if not candidate or not candidate.startswith(TOKEN_PREFIX):
        return None
    try:
        data = verify("at", candidate, prefix=TOKEN_PREFIX)
    except OAuthNotConfigured:
        return None
    owner = (data or {}).get("owner")
    return str(owner) if owner else None


# ── passphrases ───────────────────────────────────────────────────────────────

def hash_passphrase(passphrase: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", passphrase.encode(), salt, iterations)
    return f"pbkdf2-sha256${iterations}${salt.hex()}${digest.hex()}"


def check_passphrase(passphrase: str, stored: str | None) -> bool:
    try:
        algo, iters, salt_hex, digest_hex = (stored or "").split("$")
        if algo != "pbkdf2-sha256":
            return False
        computed = hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode(), bytes.fromhex(salt_hex), int(iters))
        return hmac.compare_digest(computed.hex(), digest_hex)
    except Exception:
        return False


def owner_passphrases() -> dict[str, str]:
    """OPENBRAIN_OAUTH_PASSPHRASES: JSON {owner: hash_passphrase(...)}. Empty = nobody can
    log in, which is the safe reading of "not configured"."""
    raw = os.getenv("OPENBRAIN_OAUTH_PASSPHRASES", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
