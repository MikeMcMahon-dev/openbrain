"""REST route for the retirement airlock — /propose_retirement and /api/propose_retirement.

Migration 012 built this airlock to fix a specific asymmetry: "every surface can CREATE; none
can CLOSE." It then shipped without a surface of its own, so nothing changed — `propose_retirement`
had exactly one reference in the whole repo, its own `def`. An agent could not request a removal
any more than before; the only way in was hand-importing the module in Python.

That is the same ADR-019 failure the plan/apply work kept hitting: a capability the backend
supports and no caller can reach. This route is the fix, and it is deliberately the SAME shape as
plan_ingest_route — one endpoint serving the stdio server, both Action specs, and any direct
caller, so the capability cannot exist on one surface and be missing from another.

WRITES NOTHING TO THE VAULT. It appends a row to `retirement_requests` with status='pending' and
stops. Removal happens only when a human runs `scripts/retirement_review.py execute` against an
approved request — an agent may ask, never act.
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
    # Adapters wrap arguments differently — tool_input (ChatGPT), input (Claude tool_use),
    # arguments (raw MCP). Accept every shape so one route really does serve every surface.
    for envelope in ("tool_input", "input", "arguments"):
        if isinstance(payload.get(envelope), dict):
            payload = dict(payload[envelope])
            break

    target_id = (payload.get("target_id") or "").strip()
    if not target_id:
        return response_payload(
            400,
            {
                "error": "validation_error",
                "message": "target_id is required — the id of the row you want removed.",
                "status": 400,
            },
        )

    owner, _tenant_id = request_context(metadata)

    from api.retirement_request import propose_retirement

    result = propose_retirement(
        target_id,
        rationale=payload.get("rationale") or "",
        requested_by=owner,
        method=(payload.get("method") or "retire"),
        reason_code=(payload.get("reason_code") or "manual"),
    )
    return response_payload(int(result.get("code", 200)), result)
