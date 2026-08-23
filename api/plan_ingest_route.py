"""REST route for the ingest plan — /plan_ingest and /api/plan_ingest.

The hosted MCP surface calls `build_plan()` in-process, but nothing else can: the stdio server is
a thin HTTP client, and a Custom GPT Action can only speak OpenAPI over HTTP. Both need a path.
One endpoint serves all three consumers, so the plan can never be available on one surface and
missing on another — which is precisely the drift that left `system`/`component` unreachable from
MCP for weeks while the backend already accepted them.

READ-ONLY. Writes nothing, ever. It reads the living docs in scope, reports what a commit would
supersede, and returns a short-lived token bound to the exact content.
"""
from __future__ import annotations

from api._openbrain_api import (
    parse_request,
    request_context,
    require_auth,
    response_payload,
    validate_method,
)


def handler(request):
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

    auth_error = require_auth(metadata)
    if auth_error:
        return auth_error

    payload = dict(payload or {})
    # The ChatGPT/Claude adapters wrap arguments in tool_input; accept either shape so the same
    # route works for a raw POST and for a tool envelope.
    if isinstance(payload.get("tool_input"), dict):
        payload = dict(payload["tool_input"])

    source = (payload.get("source") or "").strip()
    if not source:
        return response_payload(
            400,
            {
                "error": "validation_error",
                "message": "source is required — pass the exact content you intend to ingest.",
                "status": 400,
            },
        )

    owner, _tenant_id = request_context(metadata)

    from api.ingest_plan import build_plan

    plan = build_plan(
        source,
        owner,
        system=payload.get("system") or None,
        component=payload.get("component") or None,
    )
    return response_payload(200, plan)
