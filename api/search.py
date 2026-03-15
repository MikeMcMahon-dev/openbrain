from __future__ import annotations

from api._openbrain_api import parse_request, response_payload, search_payload, validate_method


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
        payload = dict(payload or {})
        if not payload:
            payload["query"] = (metadata.get("query") or {}).get("query", "")
            payload["n_results"] = (metadata.get("query") or {}).get("n_results")
            payload["owner"] = (metadata.get("query") or {}).get("owner")
            payload["tenant_id"] = (metadata.get("query") or {}).get("tenant_id")
    status, body = search_payload(payload, metadata)
    return response_payload(status, body)
