"""Containment must deny issuance even with configured owners and alternate routes."""
import importlib.util
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from api import app, oauth

ROOT = Path(__file__).resolve().parents[1]
PATHS = [prefix + path + suffix
         for prefix in ("", "/api")
         for path in ("/authorize", "/token", "/.well-known/oauth-authorization-server")
         for suffix in ("", "/")]


@pytest.fixture(autouse=True)
def synthetic_auth(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "synthetic-owner-token")
    monkeypatch.setenv("OPENBRAIN_TOKEN_OWNER_MAP",
                       json.dumps({"synthetic-owner-token": "synthetic-owner"}))


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("method", ["GET", "POST", "OPTIONS"])
def test_application_denies_every_retired_route(path, method):
    response = app.handler({"path": path, "method": method,
                            "queryStringParameters": {
                                "response_type": "code", "client_id": "synthetic-owner",
                                "redirect_uri": "https://callback.invalid/",
                                "code_challenge": "synthetic-verifier",
                                "code_challenge_method": "plain"},
                            "body": {"grant_type": "authorization_code",
                                     "code": "previously-issued-code",
                                     "code_verifier": "synthetic-verifier"}})
    assert response["statusCode"] == 410
    assert json.loads(response["body"]) == {"error": "oauth_disabled"}
    assert "Location" not in response["headers"]
    assert response["headers"]["Cache-Control"] == "no-store"


@pytest.mark.parametrize("handler", [oauth.handle_authorize, oauth.handle_token,
                                     oauth.handle_discovery])
def test_direct_handlers_cannot_issue_or_discover_tokens(handler):
    assert handler(None)["statusCode"] == 410


@pytest.mark.parametrize("path", PATHS)
def test_edge_denial_precedes_api_and_spa_routes(path):
    routes = json.loads((ROOT / "vercel.json").read_text())["routes"]
    first_match = next(route for route in routes if re.fullmatch(route["src"], path))
    assert first_match["status"] == 410
    assert "dest" not in first_match
    assert not first_match.get("continue")


def test_existing_bearer_query_route_remains_available():
    with patch("api.chatgpt.query_payload", return_value=(200, {"results": []})) as query:
        result = app.handler({"path": "/openbrain_query", "method": "POST",
                              "headers": {"Authorization": "Bearer synthetic-owner-token"},
                              "body": {"query": "synthetic query"}})
    assert result["statusCode"] == 200
    query.assert_called_once()


@pytest.mark.parametrize("status,location,expected_failures",
                         [(410, "", 0), (200, "", 12), (302, "https://callback.invalid/", 12),
                          (410, "https://callback.invalid/", 12)])
def test_live_smoke_rejects_reenabled_issuer_and_redirects(status, location, expected_failures):
    spec = importlib.util.spec_from_file_location("containment_smoke",
                                                 ROOT / "scripts/smoke_checks.py")
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    class Response:
        headers = {"Location": location} if location else {}

        def __enter__(self):
            self.status = status
            return self

        def __exit__(self, *args):
            return False

    with patch.object(smoke.urllib.request, "build_opener") as build:
        build.return_value.open.return_value = Response()
        assert smoke.smoke_oauth("https://deployment.invalid") == expected_failures
