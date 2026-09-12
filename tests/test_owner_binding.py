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

