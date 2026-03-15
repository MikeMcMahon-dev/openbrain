from __future__ import annotations

from api._openbrain_api import ingest_payload, parse_request, response_payload


def handler(request):
    payload, metadata = parse_request(request)

    if metadata["method"] == "OPTIONS":
        return response_payload(200, {"ok": True})

    if metadata["method"] not in {"POST"}:
        return response_payload(
            405,
            {
                "error": "method_not_allowed",
                "message": f"Method {metadata['method']} is not supported.",
                "status": 405,
            },
        )

    status, body = ingest_payload(payload, metadata)
    return response_payload(status, body)
