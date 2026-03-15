from __future__ import annotations

from api._openbrain_api import (
    parse_request,
    query_payload,
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

    if metadata["method"] == "GET":
        status, body = query_payload(payload, metadata)
    else:
        payload = dict(payload)
        payload["mode"] = "quiz"
        status, body = query_payload(payload, metadata)

    return response_payload(status, body)
