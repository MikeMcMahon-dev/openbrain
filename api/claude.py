from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from api._openbrain_api import (
    ingest_payload,
    parse_request,
    query_payload,
    response_payload,
    validate_method,
)
from api.chatgpt import _inject_token_owner, _require_tool_auth


def _resolve_claude_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    """Unwrap Claude tool_use envelope: {type, id, name, input: {...}}"""
    if not isinstance(payload, Mapping):
        return {}

    # Claude native tool_use format: {"type": "tool_use", "name": "...", "input": {...}}
    if isinstance(payload.get("input"), Mapping):
        return payload["input"]

    # Direct payload (no envelope)
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

    normalized_payload = dict(
        _resolve_claude_payload(payload) if isinstance(payload, Mapping) else {}
    )

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
