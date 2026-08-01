"""GET /api/cron/contradiction_detect — Vercel Cron handler (ADR-018 P4).

DORMANT by design: this handler is built and routed, but NOT scheduled in vercel.json. The P0
baseline found only ~9 same-system candidate pairs (mostly dismissable), so a nightly scan is
over-built for the current corpus. Enable it when contradiction volume climbs by adding ONE
entry to vercel.json crons:

    { "path": "/api/cron/contradiction_detect", "schedule": "0 5 * * *" }

It refreshes the pending review queue (never re-flags a dismissed/confirmed pair). Confirmation
stays a HUMAN action via scripts/contradiction_review.py — this only surfaces candidates.

Vercel sets CRON_SECRET automatically and sends it as: Authorization: Bearer <CRON_SECRET>.
"""
from __future__ import annotations

import os
from typing import Any

from api._openbrain_api import _stringify_headers, parse_request, response_payload
from api.contradiction_detect import detect_candidates


def handler(request) -> dict[str, Any]:
    _, metadata = parse_request(request)

    if metadata["method"] == "OPTIONS":
        return response_payload(200, {"ok": True})

    if metadata["method"] not in {"GET", "POST"}:
        return response_payload(405, {"error": "method_not_allowed", "status": 405})

    cron_secret = os.getenv("CRON_SECRET", "")
    if cron_secret:
        headers = _stringify_headers(metadata.get("headers"))
        auth = headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            auth = auth.split(" ", 1)[1].strip()
        if auth != cron_secret:
            return response_payload(401, {"error": "unauthorized", "status": 401})

    try:
        rep = detect_candidates()
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc), "status": 500})

    return response_payload(200, {
        "status": "ok",
        "inserted": rep["inserted"],
        "pending": rep["pending"],
        "threshold": rep["threshold"],
    })
