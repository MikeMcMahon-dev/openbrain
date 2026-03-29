"""GET /api/cron/session_report — Vercel Cron Job handler.

Vercel sets CRON_SECRET automatically and sends it as:
    Authorization: Bearer <CRON_SECRET>

REPORT_CONFIGS env var: JSON array of {"owner": "...", "recipients": ["..."]}
    e.g. [{"owner": "annie", "recipients": ["mike@example.com"]}]

Schedule is defined in vercel.json crons config (currently 0 21 * * * = 9pm UTC).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from api._openbrain_api import _stringify_headers, parse_request, response_payload
from api.session_report import _build_report_html, _fetch_session_rows, _send_email


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

    raw_configs = os.getenv("REPORT_CONFIGS", "").strip()
    if not raw_configs:
        return response_payload(
            200, {"status": "skipped", "message": "REPORT_CONFIGS not configured."}
        )

    try:
        configs = json.loads(raw_configs)
    except Exception:
        return response_payload(500, {"error": "REPORT_CONFIGS is not valid JSON", "status": 500})

    if not isinstance(configs, list):
        return response_payload(
            500, {"error": "REPORT_CONFIGS must be a JSON array", "status": 500}
        )

    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    results = []

    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        owner = (cfg.get("owner") or "").strip()
        recipients = cfg.get("recipients") or []
        if not owner or not isinstance(recipients, list) or not recipients:
            results.append({
                "owner": owner or "(missing)",
                "status": "skipped",
                "reason": "missing owner or recipients",
            })
            continue

        rows = _fetch_session_rows(owner, date)
        if not rows:
            results.append({"owner": owner, "status": "skipped", "query_count": 0})
            continue

        html = _build_report_html(owner, date, rows)
        subject = f"[OpenBrain] Study Session — {owner} — {date}"
        sent, detail = _send_email(recipients, subject, html)
        results.append({
            "owner": owner,
            "status": "sent" if sent else "email_failed",
            "query_count": len(rows),
            "recipients": recipients,
            "resend_id": detail if sent else None,
            "error": None if sent else detail,
        })

    return response_payload(200, {"date": date, "results": results})
