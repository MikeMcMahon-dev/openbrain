from __future__ import annotations

import json

from api._openbrain_api import request_context, require_auth


def _metadata(owner_header: str = "anneliesepaige") -> dict:
    return {
        "method": "POST",
        "headers": {
            "authorization": "Bearer mike-token",
            "x-openbrain-owner": owner_header,
        },
    }


def test_token_owner_overwrites_spoofed_header(monkeypatch):
    monkeypatch.setenv(
        "OPENBRAIN_TOKEN_OWNER_MAP",
        json.dumps({"mike-token": "mike.mcmahon67", "beth-token": "snapple01"}),
    )
    metadata = _metadata()

    assert require_auth(metadata) is None
    assert request_context(metadata)[0] == "mike.mcmahon67"
    assert metadata["headers"]["x-openbrain-owner"] == "mike.mcmahon67"


def test_invalid_token_cannot_bind_an_owner(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_TOKEN_OWNER_MAP", json.dumps({"mike-token": "mike.mcmahon67"}))
    metadata = _metadata()
    metadata["headers"]["authorization"] = "Bearer forged"

    response = require_auth(metadata)
    assert response is not None
    assert response["statusCode"] == 401



# ── ChatGPT / Claude / MCP adapter path (api.chatgpt._inject_token_owner) ────────────────
# Regression: the adapter used to keep a caller-supplied x-openbrain-owner, so any mapped
# token could act as any other owner. Driven through the real handler with only the
# DB-touching query_payload stubbed, so the binding is tested where it is actually used.

import pytest  # noqa: E402

import api.chatgpt as chatgpt  # noqa: E402

_TOKENS = {"annie-token": "anneliesepaige", "mike-token": "mike.mcmahon67"}


@pytest.mark.parametrize("spoof_headers", [
    {"x-openbrain-owner": "mike.mcmahon67"},
    {"X-OpenBrain-Owner": "mike.mcmahon67"},
    {"x-openbrain-owner": "x", "X-OPENBRAIN-OWNER": "mike.mcmahon67"},
    {"x-openbrain-user-login": "mike.mcmahon67"},
    {"x-user-id": "mike.mcmahon67"},
    {"x-slack-user-id": "mike.mcmahon67"},
])
@pytest.mark.parametrize("tool_mode", ["query", "generate_quiz", "ingest"])
def test_adapter_token_owner_beats_spoofed_headers(monkeypatch, spoof_headers, tool_mode):
    monkeypatch.setenv("OPENBRAIN_TOKEN_OWNER_MAP", json.dumps(_TOKENS))
    seen = {}

    def _capture(_payload, metadata):
        seen["owner"] = request_context(metadata)[0]
        return 200, {}

    monkeypatch.setattr(chatgpt, "query_payload", _capture)
    monkeypatch.setattr(chatgpt, "ingest_payload", _capture)
    request = {
        "method": "POST",
        "headers": {"authorization": "Bearer annie-token", **spoof_headers},
        "body": json.dumps({"query": "q", "source": "s", "source_type": "text"}),
    }

    response = chatgpt.handler(request, tool_mode=tool_mode)

    assert response["statusCode"] == 200
    assert seen["owner"] == "anneliesepaige"


def test_require_auth_strips_case_variant_owner_header(monkeypatch):
    monkeypatch.setenv("OPENBRAIN_TOKEN_OWNER_MAP", json.dumps(_TOKENS))
    metadata = {"method": "POST", "headers": {
        "authorization": "Bearer annie-token",
        "x-openbrain-owner": "x",
        "X-OpenBrain-Owner": "mike.mcmahon67",
    }}

    assert require_auth(metadata) is None
    assert request_context(metadata)[0] == "anneliesepaige"
