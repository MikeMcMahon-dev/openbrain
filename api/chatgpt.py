from __future__ import annotations

import json as _json
import os
from collections.abc import Mapping
from typing import Any

from api._openbrain_api import ingest_payload, parse_request, query_payload, response_payload, validate_method


def _stringify_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        return {}

    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        normalized[key.lower()] = str(value) if value is not None else ""
    return normalized


def _get_token_owner_map() -> dict[str, str]:
    """Load OPENBRAIN_TOKEN_OWNER_MAP — JSON mapping token → owner string."""
    raw = os.getenv("OPENBRAIN_TOKEN_OWNER_MAP", "").strip()
    if not raw:
        return {}
    try:
        result = _json.loads(raw)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _require_tool_auth(metadata: Mapping[str, Any] | None) -> tuple[bool, str | None, str | None]:
    """
    Validate the bearer token from request headers.

    Returns (authorized, error_reason, resolved_owner).
    resolved_owner is set when the token maps to a specific owner via
    OPENBRAIN_TOKEN_OWNER_MAP; None means use the deployment default.
    """
    access_token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN")
    token_map = _get_token_owner_map()

    if not access_token and not token_map:
        return True, None, None

    headers = _stringify_headers(metadata.get("headers") if isinstance(metadata, Mapping) else None)
    candidate = (
        headers.get("authorization")
        or headers.get("x-openbrain-tool-token")
        or headers.get("x-openbrain-tool-key")
    )

    if not candidate:
        return False, "Tool access token required.", None

    if candidate.startswith("Bearer ") or candidate.startswith("bearer "):
        candidate = candidate.split(" ", 1)[1].strip()

    # Per-user token map takes priority — resolves both auth and owner.
    if token_map and candidate in token_map:
        return True, None, token_map[candidate]

    # Fall back to shared token (admin / legacy use).
    if access_token and candidate == access_token:
        return True, None, None

    return False, "Invalid tool access token.", None


def _inject_token_owner(metadata: dict[str, Any], owner: str) -> None:
    """Inject token-resolved owner as x-openbrain-owner if not already set."""
    headers = metadata.get("headers")
    if not isinstance(headers, dict):
        headers = {}
        metadata["headers"] = headers
    if not headers.get("x-openbrain-owner"):
        headers["x-openbrain-owner"] = owner


def _resolve_tool_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        return {}

    if isinstance(payload.get("tool_input"), Mapping):
        return payload["tool_input"]
    if isinstance(payload.get("input"), Mapping):
        return payload["input"]
    if isinstance(payload.get("arguments"), Mapping):
        return payload["arguments"]

    return payload


def handler(request, *, tool_mode: str) -> dict[str, Any]:
    payload, metadata = parse_request(request)

    if metadata["method"] == "OPTIONS":
        return response_payload(200, {"ok": True})

    if not validate_method(metadata["method"]):
        return response_payload(
            405,
            {
                "error": "method_not_allowed",
                "message": f"Method {metadata['method']} is not supported.",
                "status": 405,
            },
        )

    is_authorized, reason, resolved_owner = _require_tool_auth(metadata)
    if not is_authorized:
        return response_payload(
            401,
            {
                "error": "unauthorized",
                "message": reason,
                "status": 401,
            },
        )

    if resolved_owner:
        _inject_token_owner(metadata, resolved_owner)

    normalized_payload = dict(_resolve_tool_payload(payload) if isinstance(payload, Mapping) else {})

    if tool_mode in {"query", "generate_quiz", "generate_flashcards"}:
        if tool_mode == "generate_quiz":
            normalized_payload["mode"] = "quiz"
        elif tool_mode == "generate_flashcards":
            normalized_payload["mode"] = "flashcards"
        status, body = query_payload(normalized_payload, metadata)
    elif tool_mode == "ingest":
        status, body = ingest_payload(normalized_payload, metadata)
    else:
        return response_payload(
            400,
            {
                "error": "unsupported_tool_mode",
                "message": f"Unsupported tool mode '{tool_mode}'.",
                "status": 400,
            },
        )

    return response_payload(status, body)
