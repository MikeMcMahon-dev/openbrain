"""OAuth 2.0 authorization server for MCP HTTP connector (Claude.ai).

Implements authorization code flow with PKCE. No consent screen — single-family
system with pre-authorized owners. Authorization codes are stateless HMAC-signed
tokens; no DB required.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from typing import Any

from api._openbrain_api import _get_token_owner_map, cors_headers

# ── Helpers ───────────────────────────────────────────────────────────────────

def _signing_secret() -> bytes:
    return os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "openbrain-oauth-secret").encode()


def _redirect(location: str, extra_headers: dict | None = None) -> dict[str, Any]:
    headers = {**cors_headers(), "Location": location}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": 302, "headers": headers, "body": ""}


def _json(status: int, body: dict) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {**cors_headers(), "Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _error_redirect(redirect_uri: str, error: str, state: str) -> dict[str, Any]:
    if not redirect_uri:
        return _json(400, {"error": error})
    params: dict[str, str] = {"error": error}
    if state:
        params["state"] = state
    return _redirect(redirect_uri + "?" + urllib.parse.urlencode(params))


# ── Authorization code (stateless HMAC-signed) ────────────────────────────────

def _encode_code(client_id: str, redirect_uri: str, code_challenge: str, method: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": method,
        "exp": int(time.time()) + 300,  # 5-minute TTL
    }).encode()).decode().rstrip("=")
    sig = hmac.new(_signing_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _decode_code(code: str) -> dict | None:
    try:
        payload_b64, sig = code.rsplit(".", 1)
        expected = hmac.new(_signing_secret(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        padding = (4 - len(payload_b64) % 4) % 4
        data = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * padding).decode())
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


# ── PKCE verification ─────────────────────────────────────────────────────────

def _verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if method == "S256":
        computed = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()
        ).decode().rstrip("=")
        return hmac.compare_digest(computed, challenge.rstrip("="))
    if method == "plain":
        return hmac.compare_digest(verifier, challenge)
    return False


# ── Owner → token lookup ──────────────────────────────────────────────────────

def _owner_to_token(owner: str) -> str | None:
    return next((t for t, o in _get_token_owner_map().items() if o == owner), None)


# ── Route handlers ────────────────────────────────────────────────────────────

def handle_discovery(request: dict) -> dict[str, Any]:
    """GET /.well-known/oauth-authorization-server"""
    host = (request.get("headers") or {}).get("host", "openbrain-rouge.vercel.app")
    base = f"https://{host}"
    return _json(200, {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


def handle_authorize(request: dict) -> dict[str, Any]:
    """GET /authorize — issue authorization code and redirect to callback."""
    params = request.get("queryStringParameters") or request.get("query") or {}

    response_type       = params.get("response_type", "")
    client_id           = params.get("client_id", "")
    redirect_uri        = params.get("redirect_uri", "")
    code_challenge      = params.get("code_challenge", "")
    code_challenge_method = params.get("code_challenge_method", "S256")
    state               = params.get("state", "")

    if response_type != "code":
        return _error_redirect(redirect_uri, "unsupported_response_type", state)
    if not client_id or not redirect_uri or not code_challenge:
        return _error_redirect(redirect_uri, "invalid_request", state)

    known_owners = set(_get_token_owner_map().values())
    if client_id not in known_owners:
        return _error_redirect(redirect_uri, "unauthorized_client", state)

    code = _encode_code(client_id, redirect_uri, code_challenge, code_challenge_method)
    callback = redirect_uri + "?" + urllib.parse.urlencode({"code": code, "state": state})
    return _redirect(callback)


def handle_token(request: dict) -> dict[str, Any]:
    """POST /token — exchange authorization code for Bearer access token."""
    body = request.get("body") or {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            try:
                body = dict(urllib.parse.parse_qsl(body))
            except Exception:
                body = {}

    grant_type    = body.get("grant_type", "")
    code          = body.get("code", "")
    redirect_uri  = body.get("redirect_uri", "")
    code_verifier = body.get("code_verifier", "")

    if grant_type != "authorization_code":
        return _json(400, {"error": "unsupported_grant_type"})
    if not code or not code_verifier:
        return _json(400, {"error": "invalid_request"})

    code_data = _decode_code(code)
    if not code_data:
        return _json(400, {"error": "invalid_grant", "error_description": "Invalid or expired"})
    if code_data.get("redirect_uri") != redirect_uri:
        return _json(400, {"error": "invalid_grant", "error_description": "redirect_uri mismatch"})
    method = code_data.get("code_challenge_method", "S256")
    if not _verify_pkce(code_verifier, code_data["code_challenge"], method):
        return _json(400, {"error": "invalid_grant", "error_description": "PKCE failed"})

    token = _owner_to_token(code_data.get("client_id", ""))
    if not token:
        return _json(400, {"error": "invalid_client"})

    return _json(200, {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 7776000,  # 90 days
    })
