"""OAuth surface (api/oauth.py, api/oauth_tokens.py).

The old /authorize issued an owner-bound code to anyone who knew an owner name (OB-1,
2026-09-12). These tests pin the replacement: a code is issued only after a passphrase
verifies, only to a registered redirect, and the resulting token is accepted by the tool
auth as that owner — and nothing else is.

Run: cd tests && ../.venv/bin/python -m pytest test_oauth.py -q
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.parse
from unittest.mock import patch

import pytest

from api import _openbrain_api as core
from api import oauth, oauth_tokens

OWNER = "mike.mcmahon67"
OTHER = "anneliesepaige"
PASS = "correct horse battery staple"
REDIRECT = "https://chatgpt.com/connector_platform_oauth_redirect"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_OAUTH_SIGNING_SECRET", secrets.token_hex(16))
    monkeypatch.setenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "static-tool-token")
    monkeypatch.setenv("OPENBRAIN_TOKEN_OWNER_MAP",
                       json.dumps({"tok-mike": OWNER, "tok-annie": OTHER}))
    monkeypatch.setenv("OPENBRAIN_OAUTH_PASSPHRASES", json.dumps({
        OWNER: oauth_tokens.hash_passphrase(PASS, iterations=1000)}))


def _pkce():
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                         ).decode().rstrip("=")
    return verifier, challenge


def _register(uris=(REDIRECT,)) -> str:
    resp = oauth.handle_register({"method": "POST", "body": json.dumps(
        {"redirect_uris": list(uris), "client_name": "ChatGPT"})})
    assert resp["statusCode"] == 201, resp
    return json.loads(resp["body"])["client_id"]


def _authorize_get(client_id, challenge, redirect=REDIRECT):
    return oauth.handle_authorize({"method": "GET", "queryStringParameters": {
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "xyz"}})


def _authorize_post(client_id, challenge, username, passphrase, redirect=REDIRECT):
    form = urllib.parse.urlencode({
        "client_id": client_id, "redirect_uri": redirect, "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "xyz",
        "username": username, "passphrase": passphrase})
    return oauth.handle_authorize({"method": "POST", "body": form})


def _code_from(resp) -> str:
    assert resp["statusCode"] == 302, resp
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(resp["headers"]["Location"]).query)
    assert q["state"] == ["xyz"]
    return q["code"][0]


def _token(code, verifier, client_id=None, redirect=REDIRECT):
    body = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "redirect_uri": redirect}
    if client_id:
        body["client_id"] = client_id
    return oauth.handle_token({"method": "POST", "body": urllib.parse.urlencode(body)})


def _auth_owner(bearer: str):
    ok, _reason, owner = core._require_tool_auth({"headers": {"authorization": f"Bearer {bearer}"}})
    return ok, owner


# ── the happy path, which is the ChatGPT connector flow ──────────────────────

def test_register_login_token_and_tool_auth_end_to_end():
    client_id = _register()
    verifier, challenge = _pkce()

    page = _authorize_get(client_id, challenge)
    assert page["statusCode"] == 200
    assert 'name="passphrase"' in page["body"], "GET must render a login, not issue a code"

    code = _code_from(_authorize_post(client_id, challenge, OWNER, PASS))
    tok = _token(code, verifier, client_id=client_id)
    assert tok["statusCode"] == 200, tok
    access = json.loads(tok["body"])["access_token"]
    assert access.startswith(oauth_tokens.TOKEN_PREFIX)

    assert _auth_owner(access) == (True, OWNER)


def test_discovery_advertises_registration_and_resource_metadata():
    disc = json.loads(oauth.handle_discovery({"headers": {"host": "ob.example"}})["body"])
    assert disc["registration_endpoint"] == "https://ob.example/register"
    assert disc["code_challenge_methods_supported"] == ["S256"]
    prm = json.loads(oauth.handle_protected_resource({"headers": {"host": "ob.example"}})["body"])
    assert prm["authorization_servers"] == ["https://ob.example"]
    assert 'resource_metadata="https://ob.example/.well-known/oauth-protected-resource"' in \
        oauth.www_authenticate({"headers": {"host": "ob.example"}})["WWW-Authenticate"]


# ── the gate: what must NOT get a code ───────────────────────────────────────

def test_wrong_passphrase_is_refused_and_not_redirected():
    client_id = _register()
    _, challenge = _pkce()
    resp = _authorize_post(client_id, challenge, OWNER, "nope")
    assert resp["statusCode"] == 401
    assert "Location" not in resp["headers"]
    assert "incorrect" in resp["body"]


def test_owner_name_alone_no_longer_grants_a_code():
    """OB-1: a known owner name with no passphrase entry is a name, not a credential."""
    client_id = _register()
    _, challenge = _pkce()
    resp = _authorize_post(client_id, challenge, OTHER, "anything")
    assert resp["statusCode"] == 401


def test_no_passphrases_configured_means_nobody_can_log_in(monkeypatch):
    monkeypatch.delenv("OPENBRAIN_OAUTH_PASSPHRASES")
    client_id = _register()
    _, challenge = _pkce()
    assert _authorize_post(client_id, challenge, OWNER, PASS)["statusCode"] == 401


def test_unregistered_redirect_is_rendered_not_redirected():
    """The old code redirected errors to whatever redirect_uri was supplied: an open
    redirector. An unvalidated redirect_uri must never appear in a Location header."""
    client_id = _register()
    _, challenge = _pkce()
    evil = "https://evil.example/cb"
    resp = _authorize_get(client_id, challenge, redirect=evil)
    assert resp["statusCode"] == 400
    assert "Location" not in resp["headers"]
    resp = _authorize_post(client_id, challenge, OWNER, PASS, redirect=evil)
    assert resp["statusCode"] == 400
    assert "Location" not in resp["headers"]


def test_unknown_client_is_rejected():
    _, challenge = _pkce()
    assert _authorize_get("obc_forged.deadbeef", challenge)["statusCode"] == 400
    assert _authorize_get("nobody", challenge)["statusCode"] == 400


def test_legacy_owner_client_is_held_to_the_redirect_host_allowlist():
    _, challenge = _pkce()
    ok = _authorize_get(OWNER, challenge, redirect="https://claude.ai/api/mcp/auth_callback")
    assert ok["statusCode"] == 200
    bad = _authorize_get(OWNER, challenge, redirect="https://evil.example/cb")
    assert bad["statusCode"] == 400


def test_cimd_client_id_is_fetched_and_validated():
    url = "https://chatgpt.com/.well-known/client.json"
    meta = {"client_id": url, "redirect_uris": [REDIRECT]}
    with patch.object(oauth, "_fetch_cimd", return_value=meta):
        client = oauth.resolve_client(url)
    assert client == {"redirect_uris": [REDIRECT], "name": "chatgpt.com"}
    borrowed = {"client_id": "https://other", "redirect_uris": [REDIRECT]}
    with patch.object(oauth, "_fetch_cimd", return_value=borrowed):
        assert oauth.resolve_client(url) is None, "metadata must name itself as the client_id"


def test_register_requires_https_redirects():
    resp = oauth.handle_register({"method": "POST", "body": json.dumps(
        {"redirect_uris": ["http://localhost/cb"]})})
    assert resp["statusCode"] == 400


def test_register_refuses_redirects_off_the_host_allowlist():
    """Open registration is safe only because a client can never point anywhere but
    ChatGPT/Claude: a phishing client with its own redirect cannot be registered at all."""
    resp = oauth.handle_register({"method": "POST", "body": json.dumps(
        {"redirect_uris": ["https://evil.example/cb"], "client_name": "Totally ChatGPT"})})
    assert resp["statusCode"] == 400
    assert "evil.example" in json.loads(resp["body"])["error_description"]
    # a mixed list is refused whole, not trimmed to the good entries
    resp = oauth.handle_register({"method": "POST", "body": json.dumps(
        {"redirect_uris": [REDIRECT, "https://evil.example/cb"]})})
    assert resp["statusCode"] == 400


def test_host_allowlist_is_configurable_and_matches_subdomains(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_OAUTH_REDIRECT_HOSTS", "example.org")
    assert oauth.host_allowed("https://app.example.org/cb")
    assert oauth.host_allowed("https://example.org/cb")
    assert not oauth.host_allowed("https://example.org.evil.net/cb")
    assert not oauth.host_allowed("https://chatgpt.com/cb")


def test_login_page_names_the_client_and_the_return_host():
    client_id = _register()
    _, challenge = _pkce()
    body = _authorize_get(client_id, challenge)["body"]
    assert "<b>ChatGPT</b> is asking for access" in body
    assert "<b>chatgpt.com</b>" in body


# ── token exchange ────────────────────────────────────────────────────────────

def test_pkce_mismatch_and_redirect_mismatch_are_refused():
    client_id = _register()
    verifier, challenge = _pkce()
    code = _code_from(_authorize_post(client_id, challenge, OWNER, PASS))
    assert json.loads(_token(code, "wrong-verifier")["body"])["error"] == "invalid_grant"
    other_redirect = _token(code, verifier, redirect="https://chatgpt.com/other")
    assert json.loads(other_redirect["body"])["error"] == "invalid_grant"
    other_client = _token(code, verifier, client_id="obc_someone.else")
    assert json.loads(other_client["body"])["error"] == "invalid_grant"


def test_code_cannot_be_used_as_a_bearer_and_client_id_cannot_be_a_code():
    client_id = _register()
    verifier, challenge = _pkce()
    code = _code_from(_authorize_post(client_id, challenge, OWNER, PASS))
    assert _auth_owner(code) == (False, None)
    assert oauth_tokens.verify_access_token(code) is None
    assert json.loads(_token(client_id, verifier)["body"])["error"] == "invalid_grant"


def test_expired_token_is_refused():
    tok = oauth_tokens.issue_access_token(OWNER, "obc_x", ttl=-1)
    assert oauth_tokens.verify_access_token(tok) is None
    assert _auth_owner(tok) == (False, None)


def test_token_signed_with_another_secret_is_refused(monkeypatch):
    tok = oauth_tokens.issue_access_token(OWNER, "obc_x")
    monkeypatch.setenv("OPENBRAIN_OAUTH_SIGNING_SECRET", "rotated")
    assert oauth_tokens.verify_access_token(tok) is None, "rotating the secret revokes"


def test_no_signing_secret_fails_closed(monkeypatch):
    monkeypatch.delenv("OPENBRAIN_OAUTH_SIGNING_SECRET")
    monkeypatch.delenv("OPENBRAIN_TOOL_ACCESS_TOKEN")
    resp = oauth.handle_register({"method": "POST",
                                  "body": json.dumps({"redirect_uris": [REDIRECT]})})
    assert resp["statusCode"] == 503
    with pytest.raises(oauth_tokens.OAuthNotConfigured):
        oauth_tokens.sign("at", {"owner": OWNER})


def test_static_bearers_still_work_unchanged():
    assert _auth_owner("tok-annie") == (True, OTHER)
    assert _auth_owner("static-tool-token") == (True, None)
    assert _auth_owner("garbage") == (False, None)


def test_passphrase_hash_roundtrip():
    h = oauth_tokens.hash_passphrase("s3cret", iterations=1000)
    assert oauth_tokens.check_passphrase("s3cret", h)
    assert not oauth_tokens.check_passphrase("S3cret", h)
    assert not oauth_tokens.check_passphrase("s3cret", None)
    assert not oauth_tokens.check_passphrase("s3cret", "md5$x$y$z")


def test_code_ttl_is_short():
    client_id = _register()
    verifier, challenge = _pkce()
    code = _code_from(_authorize_post(client_id, challenge, OWNER, PASS))
    with patch.object(time, "time", return_value=time.time() + oauth_tokens.CODE_TTL + 5):
        assert json.loads(_token(code, verifier)["body"])["error"] == "invalid_grant"


def test_env_is_isolated():
    assert os.getenv("OPENBRAIN_OAUTH_PASSPHRASES")


# ── the wire: what the browser actually receives ─────────────────────────────

def _served(response: dict) -> list[tuple[str, str]]:
    """Run a handler response through api.index._write_response and capture the headers
    as sent. The handler's headers were right; the writer appended a second Content-Type."""
    from api import index

    class _Fake:
        def __init__(self):
            self.headers: list[tuple[str, str]] = []
            self.status = None

            class _W:
                def write(self, _b):
                    pass
            self.wfile = _W()

        def send_response(self, status):
            self.status = status

        def send_header(self, k, v):
            self.headers.append((k, v))

        def end_headers(self):
            pass

    fake = _Fake()
    index._write_response(fake, response)
    return fake.headers


def test_login_page_is_served_as_html_exactly_once():
    client_id = _register()
    _, challenge = _pkce()
    headers = _served(_authorize_get(client_id, challenge))
    cts = [v for k, v in headers if k.lower() == "content-type"]
    assert cts == ["text/html; charset=utf-8"], cts


def test_json_responses_still_default_to_json():
    headers = _served({"statusCode": 200, "body": "{}"})
    cts = [v for k, v in headers if k.lower() == "content-type"]
    assert cts == ["application/json"]
