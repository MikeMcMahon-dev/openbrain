from __future__ import annotations

from api._openbrain_api import (
    fetch_payload,
    parse_request,
    require_auth,
    response_payload,
    validate_method,
)


def handler(request):
    """Stage-2 fetch endpoint (ADR-016): full note text by id, owner-scoped.
    Same auth + owner resolution as /search and /query, so the owner used for
    scoping is the authenticated one, never client-supplied."""
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

    status, body = fetch_payload(payload, metadata)
    return response_payload(status, body)
