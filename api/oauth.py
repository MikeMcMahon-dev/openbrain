"""OAuth 2.1 authorization server for the MCP connector surface (claude.ai, ChatGPT).

Authorization-code flow with PKCE (S256 only), a real login step, and stateless signed
credentials throughout — no DB, no sessions.

What changed and why (2026-09-13):
  * /authorize used to redirect straight back with a code whenever `client_id` was a known
    owner name. The owner name was the only gate, and owner names are guessable (an email
    local-part). It now renders a login form and issues the code only after the owner's
    passphrase verifies (OPENBRAIN_OAUTH_PASSPHRASES, hashes from scripts/oauth_passphrase.py).
  * ChatGPT's connector does not let a user type a client id. It registers one (RFC 7591
    dynamic registration, POST /register) or presents a Client ID Metadata Document URL. Both
    are supported; the hand-configured claude.ai connector (client_id = owner name) still works
    but is held to a redirect-host allowlist.
  * Tokens are signed and bound to (owner, client) with a 90-day expiry instead of handing
    every OAuth client the owner's static bearer. Revoking one client no longer means rotating
    the token everything else uses.
  * Errors for an unvalidated redirect_uri are rendered, not redirected — the old code was an
    open redirector.
  * RFC 9728 protected-resource metadata is served so MCP clients can discover this server
    from the 401 on /mcp/messages.
"""
from __future__ import annotations

import html
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from api._openbrain_api import _get_token_owner_map, cors_headers
from api.oauth_tokens import (
    CLIENT_PREFIX,
    CODE_TTL,
    OAuthNotConfigured,
    check_passphrase,
    hash_passphrase,
    issue_access_token,
    owner_passphrases,
    sign,
    verify,
)

SCOPE = "openbrain"
# A real-cost hash to verify against when the username is unknown, so the response time
# does not separate "no such owner" from "wrong passphrase".
_DUMMY_HASH = hash_passphrase("not-a-real-passphrase")
# Every redirect_uri — registered, CIMD, or legacy — must land on one of these hosts. This
# is what makes open registration safe in a single-family system: /register will sign a
# client for anyone, but a client can only ever send a code to ChatGPT or Claude, so a
# registration pointing anywhere else is refused and a phishing client cannot exist.
DEFAULT_REDIRECT_HOSTS = "chatgpt.com,openai.com,claude.ai,claude.com"


# ── response helpers ──────────────────────────────────────────────────────────

def _redirect(location: str) -> dict[str, Any]:
    return {"statusCode": 302, "headers": {**cors_headers(), "Location": location}, "body": ""}


def _json(status: int, body: dict, extra_headers: dict | None = None) -> dict[str, Any]:
    headers = {**cors_headers(), "Content-Type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def _html(status: int, body: str) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8",
                    "Cache-Control": "no-store", "X-Frame-Options": "DENY"},
        "body": body,
    }


def _base(request: dict) -> str:
    headers = {str(k).lower(): v for k, v in (request.get("headers") or {}).items()}
    host = headers.get("host") or "openbrain-rouge.vercel.app"
    return f"https://{host}"


def _params(request: dict) -> dict[str, str]:
    q = request.get("queryStringParameters") or request.get("query") or {}
    return {str(k): str(v) for k, v in q.items()}


def _form(request: dict, coerce: bool = True) -> dict[str, Any]:
    """Body as a dict: JSON or urlencoded. `coerce` flattens values to str (form fields);
    /register needs the JSON list in `redirect_uris` intact, so it passes False."""
    body = request.get("body") or {}
    if isinstance(body, (bytes, bytearray)):
        body = body.decode(errors="replace")
    parsed: dict[str, Any] = {}
    if isinstance(body, str):
        try:
            loaded = json.loads(body)
            parsed = loaded if isinstance(loaded, dict) else {}
        except Exception:
            parsed = dict(urllib.parse.parse_qsl(body, keep_blank_values=True))
    elif isinstance(body, dict):
        parsed = body
    if coerce:
        return {str(k): ("" if v is None else str(v)) for k, v in parsed.items()}
    return {str(k): v for k, v in parsed.items()}


# ── clients ───────────────────────────────────────────────────────────────────

def allowed_redirect_hosts() -> set[str]:
    raw = os.getenv("OPENBRAIN_OAUTH_REDIRECT_HOSTS", DEFAULT_REDIRECT_HOSTS)
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def host_allowed(uri: str) -> bool:
    if not uri.startswith("https://"):
        return False
    host = (urllib.parse.urlsplit(uri).hostname or "").lower()
    allowed = allowed_redirect_hosts()
    return host in allowed or any(host.endswith("." + h) for h in allowed)


def _fetch_cimd(url: str) -> dict | None:
    """Client ID Metadata Document: the client_id IS an https URL to its own metadata."""
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def resolve_client(client_id: str) -> dict[str, Any] | None:
    """What this client is allowed to do, or None if it is nobody.

    Returns {"redirect_uris": [...], "name": ...} for registered/CIMD clients (exact-match
    redirects) or {"legacy": True, "name": ...} for the owner-name clients (host allowlist).
    """
    if not client_id:
        return None
    if client_id.startswith(CLIENT_PREFIX):
        data = verify("client", client_id, prefix=CLIENT_PREFIX)
        if not data:
            return None
        return {"redirect_uris": list(data.get("ru") or []), "name": data.get("n") or ""}
    if client_id.startswith("https://"):
        meta = _fetch_cimd(client_id)
        # The document must name itself: a metadata URL that claims a different client_id
        # is somebody else's registration being borrowed.
        if not meta or meta.get("client_id") != client_id:
            return None
        uris = [u for u in (meta.get("redirect_uris") or []) if isinstance(u, str)]
        name = str(meta.get("client_name") or urllib.parse.urlsplit(client_id).hostname or "")
        return {"redirect_uris": uris, "name": name} if uris else None
    if client_id in set(_get_token_owner_map().values()):
        return {"legacy": True, "name": "Claude"}
    return None


def redirect_allowed(client: dict[str, Any], redirect_uri: str) -> bool:
    """Registered clients: exact match against what they registered. Legacy clients: any
    URI on an allowed host. Both: the host allowlist is the outer wall."""
    if not host_allowed(redirect_uri):
        return False
    if "redirect_uris" in client:
        return redirect_uri in client["redirect_uris"]
    return bool(client.get("legacy"))


def handle_register(request: dict) -> dict[str, Any]:
    """POST /register — RFC 7591 dynamic client registration, stateless.

    The client id is a signed blob of the registered redirect URIs, so /authorize can
    validate a redirect without a client table. Public clients only (PKCE, no secret).
    """
    if str(request.get("method", "POST")).upper() != "POST":
        return _json(405, {"error": "method_not_allowed"})
    body = _form(request, coerce=False)
    raw_uris = body.get("redirect_uris")
    if isinstance(raw_uris, str):
        try:
            raw_uris = json.loads(raw_uris)
        except Exception:
            raw_uris = [raw_uris]
    uris = [u for u in (raw_uris or []) if isinstance(u, str)]
    if not uris:
        return _json(400, {"error": "invalid_redirect_uri",
                           "error_description": "at least one https redirect_uri is required"})
    refused = [u for u in uris if not host_allowed(u)]
    if refused:
        return _json(400, {"error": "invalid_redirect_uri",
                           "error_description": "redirect_uri host not allowed: "
                                                + ", ".join(refused)})
    name = str(body.get("client_name") or "")[:80]
    now = int(time.time())
    try:
        client_id = sign("client", {"ru": uris, "n": name, "iat": now}, prefix=CLIENT_PREFIX)
    except OAuthNotConfigured as exc:
        return _json(503, {"error": "server_error", "error_description": str(exc)})
    return _json(201, {
        "client_id": client_id,
        "client_id_issued_at": now,
        "client_name": name,
        "redirect_uris": uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
        "scope": SCOPE,
    })


# ── discovery ─────────────────────────────────────────────────────────────────

def handle_discovery(request: dict) -> dict[str, Any]:
    """GET /.well-known/oauth-authorization-server"""
    base = _base(request)
    return _json(200, {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [SCOPE],
    })


def handle_protected_resource(request: dict) -> dict[str, Any]:
    """GET /.well-known/oauth-protected-resource (RFC 9728) — how an MCP client that got a
    401 from /mcp/messages finds the authorization server."""
    base = _base(request)
    return _json(200, {
        "resource": base,
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [SCOPE],
    })


def www_authenticate(request: dict) -> dict[str, str]:
    base = _base(request)
    return {"WWW-Authenticate":
            f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'}


# ── authorize ─────────────────────────────────────────────────────────────────

_FORM_FIELDS = ("client_id", "redirect_uri", "code_challenge", "code_challenge_method",
                "state", "scope", "resource")


def _validate_authorize(p: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    """(client, error). An error here is rendered, never redirected: until the redirect_uri
    has been checked against the client it is attacker-controlled."""
    client = resolve_client(p.get("client_id", ""))
    if client is None:
        return None, "unauthorized_client: unknown client_id"
    if not redirect_allowed(client, p.get("redirect_uri", "")):
        return None, "invalid_request: redirect_uri is not registered for this client"
    if p.get("response_type", "code") != "code":
        return None, "unsupported_response_type"
    if not p.get("code_challenge"):
        return None, "invalid_request: code_challenge is required (PKCE)"
    if p.get("code_challenge_method", "S256") != "S256":
        return None, "invalid_request: only S256 is supported"
    return client, None


_CSS = "".join(line.strip() for line in """
body{font:16px system-ui,sans-serif;background:#f6f6f4;color:#222;margin:0;
     display:grid;place-items:center;min-height:100vh}
form{background:#fff;padding:2rem;border-radius:12px;box-shadow:0 2px 12px #0002;
     width:min(360px,90vw)}
h1{font-size:1.2rem;margin:0 0 1rem}
label{display:block;margin:.75rem 0 .25rem;font-size:.9rem}
input[type=text],input[type=password]{width:100%;padding:.6rem;border:1px solid #bbb;
     border-radius:8px;font-size:1rem;box-sizing:border-box}
button{margin-top:1.25rem;width:100%;padding:.7rem;border:0;border-radius:8px;
     background:#2b4c7e;color:#fff;font-size:1rem}
.err{color:#a11;font-size:.9rem;margin:.5rem 0 0}
.muted{color:#666;font-size:.8rem;margin-top:1rem}
""".splitlines())

def _login_page(p: dict[str, str], client: dict[str, Any] | None = None,
                error: str | None = None, status: int = 200) -> dict:
    hidden = "\n".join(
        f'<input type="hidden" name="{k}" value="{html.escape(p.get(k, ""))}">'
        for k in _FORM_FIELDS)
    err = f'<p class="err">{html.escape(error)}</p>' if error else ""
    who = html.escape(p.get("username", ""))
    asking = html.escape((client or {}).get("name") or "An app")
    dest = html.escape(urllib.parse.urlsplit(p.get("redirect_uri", "")).hostname or "")
    return _html(status, f"""<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenBrain sign-in</title>
<style>
{_CSS}
</style>
<form method="post" action="/authorize" autocomplete="on">
<h1>Sign in to OpenBrain</h1>
<p class="muted"><b>{asking}</b> is asking for access. After you sign in you will be sent
back to <b>{dest}</b>. If that is not the app you are setting up, close this page.</p>
{hidden}
<label for="u">Username</label>
<input id="u" type="text" name="username" value="{who}" required autofocus>
<label for="p">Passphrase</label><input id="p" type="password" name="passphrase" required>
{err}
<button type="submit">Allow access</button>
<p class="muted">This grants the requesting app read/write access to your vault for 90 days.</p>
</form>""")


def _error_page(msg: str) -> dict:
    return _html(400, f"<!doctype html><meta charset='utf-8'><title>OpenBrain</title>"
                      f"<p style='font:16px system-ui'>Authorization request rejected: "
                      f"{html.escape(msg)}</p>")


def handle_authorize(request: dict) -> dict[str, Any]:
    """GET renders the login form; POST verifies the passphrase and issues the code."""
    method = str(request.get("method", "GET")).upper()
    p = _params(request) if method == "GET" else _form(request)
    try:
        client, err = _validate_authorize(p)
    except OAuthNotConfigured as exc:
        return _json(503, {"error": "server_error", "error_description": str(exc)})
    if err:
        return _error_page(err)

    if method == "GET":
        return _login_page(p, client)
    if method != "POST":
        return _json(405, {"error": "method_not_allowed"})

    username = p.get("username", "").strip()
    passphrase = p.get("passphrase", "")
    stored = owner_passphrases().get(username)
    ok = check_passphrase(passphrase, stored or _DUMMY_HASH)
    if not stored or not ok:
        return _login_page(p, client, error="Username or passphrase is incorrect.", status=401)

    code = sign("code", {
        "owner": username,
        "client_id": p["client_id"],
        "redirect_uri": p["redirect_uri"],
        "cc": p["code_challenge"],
        "exp": int(time.time()) + CODE_TTL,
    })
    q: dict[str, str] = {"code": code}
    if p.get("state"):
        q["state"] = p["state"]
    return _redirect(p["redirect_uri"] + "?" + urllib.parse.urlencode(q))


# ── token ─────────────────────────────────────────────────────────────────────

def _pkce_ok(verifier: str, challenge: str) -> bool:
    import base64
    import hashlib
    import hmac as _hmac
    computed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                        ).decode().rstrip("=")
    return _hmac.compare_digest(computed, challenge.rstrip("="))


def handle_token(request: dict) -> dict[str, Any]:
    """POST /token — exchange a code for a signed, client-bound access token."""
    if str(request.get("method", "POST")).upper() != "POST":
        return _json(405, {"error": "method_not_allowed"})
    b = _form(request)
    if b.get("grant_type") != "authorization_code":
        return _json(400, {"error": "unsupported_grant_type"})
    if not b.get("code") or not b.get("code_verifier"):
        return _json(400, {"error": "invalid_request"})
    try:
        data = verify("code", b["code"])
    except OAuthNotConfigured as exc:
        return _json(503, {"error": "server_error", "error_description": str(exc)})
    if not data:
        return _json(400, {"error": "invalid_grant", "error_description": "Invalid or expired"})
    if data.get("redirect_uri") != b.get("redirect_uri"):
        return _json(400, {"error": "invalid_grant", "error_description": "redirect_uri mismatch"})
    if b.get("client_id") and b["client_id"] != data.get("client_id"):
        return _json(400, {"error": "invalid_grant", "error_description": "client_id mismatch"})
    if not _pkce_ok(b["code_verifier"], data.get("cc", "")):
        return _json(400, {"error": "invalid_grant", "error_description": "PKCE failed"})

    token = issue_access_token(data["owner"], data["client_id"])
    return _json(200, {
        "access_token": token,
        "token_type": "bearer",
        "expires_in": 90 * 24 * 3600,
        "scope": SCOPE,
    })
