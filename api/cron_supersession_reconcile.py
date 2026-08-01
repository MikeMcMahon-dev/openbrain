"""GET /api/cron/supersession_reconcile — Vercel Cron Job handler (ADR-018 P3 §5).

Recomputes whether knowledge.status matches the append-only supersession_events log and appends
the result to public.supersession_reconcile_log. drift_count>0 rows ARE the alert surface — no
paging, no network boundary (runs on Vercel, writes to Supabase). Drift is a bug: recovery is
replay from the event log, not restore.

Vercel sets CRON_SECRET automatically and sends it as:
    Authorization: Bearer <CRON_SECRET>

Schedule is defined in vercel.json crons config (0 4 * * * = 10pm MDT / 4am UTC, an hour after
the session report).
"""
from __future__ import annotations

import os
from typing import Any

from api._openbrain_api import (
    _stringify_headers,
    get_db_conn,
    parse_request,
    response_payload,
)
from api.supersession_reconcile import reconcile_supersession, write_reconcile_log


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
        with get_db_conn() as conn:
            report = reconcile_supersession(conn)
            write_reconcile_log(conn, report)
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc), "status": 500})

    # 200 always (the run succeeded); drift is reported in the body and persisted to the log.
    return response_payload(200, {
        "status": "ok" if report["ok"] else "drift",
        "drift_count": report["drift_count"],
        "events": report["events"],
        "superseded": report["superseded"],
        "drift": report["drift"],
    })
