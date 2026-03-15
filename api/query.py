from __future__ import annotations

from _openbrain_api import parse_request, response_payload, validate_method, query_payload


def handler(request):
    payload, metadata = parse_request(request)

    if metadata["method"] == "OPTIONS":
        return response_payload(200, {"ok": True})

    if not validate_method(metadata["method"]):
        return response_payload(405, {
            "error": "method_not_allowed",
            "message": f"Method {metadata['method']} is not supported.",
            "status": 405,
        })

    status, body = query_payload(payload)
    return response_payload(status, body)

