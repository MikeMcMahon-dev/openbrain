"""POST /openbrain_integrity_event: a Custom GPT reports a possible tutoring-integrity event.

Write-only by design: there is no read route. Parents see events only in the nightly email.

Guards, in order. Each one refuses rather than accepting the event:
  * per-user token required. The owner comes ONLY from the token; the body has no owner
    field, and a shared/default token (no owner binding) is refused.
  * INTEGRITY_RULES must load. If it doesn't, return 503 so the GPT cannot claim the event
    was logged.
  * the token owner must be a monitored owner.
  * body: known fields only, at most 2 KB, validated by api.integrity.build_self_report.
    Priority and confidence are assigned server-side.
  * at most SELF_REPORT_HOURLY_LIMIT self-reports per owner per hour.
  * replays collapse on (owner, idempotency_key) and return status "duplicate".
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from api import integrity
from api._openbrain_api import (
    parse_request,
    request_context,
    require_auth_owner,
    response_payload,
)
from api.chatgpt import _resolve_tool_payload


def _error(status: int, error: str, message: str) -> dict[str, Any]:
    return response_payload(status, {"status": "not_logged", "error": error, "message": message})


def handler(request) -> dict[str, Any]:
    payload, metadata = parse_request(request)

    if metadata["method"] == "OPTIONS":
        return response_payload(200, {"ok": True})
    if metadata["method"] != "POST":
        return _error(405, "method_not_allowed", "Use POST.")

    auth_error, token_owner = require_auth_owner(metadata)
    if auth_error:
        return auth_error
    if not token_owner:
        return _error(403, "forbidden", "Integrity events require a per-user token.")

    try:
        rules = integrity.load_rules()
    except integrity.RulesError:
        return _error(503, "not_configured", "Integrity monitoring is not configured.")

    if not integrity.is_monitored(token_owner, rules):
        return _error(403, "forbidden", "Integrity events are not enabled for this account.")

    body = _resolve_tool_payload(payload) if isinstance(payload, Mapping) else payload
    try:
        size = len(json.dumps(body, default=str).encode())
    except Exception:
        size = integrity.SELF_REPORT_MAX_BYTES + 1
    if size > integrity.SELF_REPORT_MAX_BYTES:
        return _error(413, "payload_too_large", "Event body is too large.")

    # Deployment-default tenant only; a caller-supplied tenant header is not honoured here.
    _, tenant_id = request_context({"headers": {}})
    event, problem = integrity.build_self_report(body, token_owner, tenant_id)
    if problem:
        return _error(400, "validation_error", problem)

    try:
        if integrity.count_recent_self_reports(token_owner) >= integrity.SELF_REPORT_HOURLY_LIMIT:
            return _error(429, "rate_limited", "Too many events this hour.")
        new_ids = integrity.insert_events([event])
    except Exception:
        return _error(500, "storage_error", "The event could not be saved.")

    if not new_ids:
        return response_payload(200, {"status": "duplicate", "message": "Already logged."})
    return response_payload(201, {"status": "logged", "event_id": new_ids[0]})
