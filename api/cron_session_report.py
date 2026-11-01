"""GET /api/cron/session_report — Vercel Cron Job handler.

CRON_SECRET must be set: Vercel then sends it as `Authorization: Bearer <CRON_SECRET>`, and the
handler refuses every request without it (api._openbrain_api.require_cron_secret).

REPORT_CONFIGS env var: JSON array of {"owner": "...", "recipients": ["..."]}
    e.g. [{"owner": "annie", "recipients": ["mike@example.com"]}]

Per configured owner, every night:
  1. Integrity check (monitored owners only; see api/integrity.py). Runs the server
     heuristic, then sends any review-worthy events in a SEPARATE "worth a conversation"
     email. Events are marked reported only after their email is sent; a failed send retries
     the next night.
  2. The usage report. It is sent every day, including no-activity days (heartbeat) and
     data-read failures (failure email), so a missing email always means the cron itself
     did not run. Low-priority integrity events appear in it as one-line notes.
Then integrity events past their retention are deleted.

Schedule is defined in vercel.json crons config (currently 0 3 * * * = 9pm MDT / 3am UTC).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from api import integrity
from api._openbrain_api import parse_request, request_context, require_cron_secret, response_payload
from api.session_report import (
    ReportDataError,
    _build_failure_html,
    _build_heartbeat_html,
    _build_report_html,
    _fetch_session_rows,
    _fetch_study_notes,
    _send_email,
)

# The detection window reaches back before UTC midnight so a session that straddles the day
# boundary is seen whole. Re-scanned sessions dedupe on their idempotency key.
_LOOKBACK = timedelta(hours=3)


def _run_integrity(
    owner: str,
    date: str,
    recipients: list[str],
    rules: dict[str, Any] | None,
    rules_error: str | None,
) -> dict[str, Any]:
    if rules is None:
        return {"state": "not_running", "detail": rules_error}
    if not integrity.is_monitored(owner, rules):
        return {"state": "not_monitored"}

    tz = rules["display_timezone"]
    day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    try:
        day_end = day_start + timedelta(days=1)
        rows = integrity.fetch_query_rows(owner, day_start - _LOOKBACK, day_end)
        _, tenant_id = request_context({"headers": {}})
        integrity.insert_events(integrity.detect(rows, owner, tenant_id, rules))
        pending = integrity.fetch_unreported(owner)
    except Exception as exc:
        return {"state": "not_running", "detail": f"database error ({type(exc).__name__})"}

    review = [e for e in pending if integrity.needs_review(e, rules)]
    digest = [e for e in pending if not integrity.needs_review(e, rules)]
    info: dict[str, Any] = {
        "state": "ran",
        "review_count": len(review),
        "flag_email": None,
        "digest_lines": [integrity.event_line(e, tz) for e in digest],
        "digest_ids": [str(e["id"]) for e in digest],
    }
    if review:
        name = rules["display_names"].get(owner, owner)
        sent, detail = _send_email(
            recipients,
            f"[OpenBrain] Study Buddy — worth a conversation — {name} — {date}",
            integrity.build_flag_email_html(name, review, tz),
        )
        info["flag_email"] = "sent" if sent else f"failed: {detail}"[:200]
        if sent:
            try:
                integrity.mark_reported([str(e["id"]) for e in review])
            except Exception as exc:
                # Sent but not marked: tomorrow re-sends. Noisy, never silent.
                info["mark_error"] = type(exc).__name__
    return info


def handler(request) -> dict[str, Any]:
    _, metadata = parse_request(request)

    if metadata["method"] == "OPTIONS":
        return response_payload(200, {"ok": True})

    if metadata["method"] not in {"GET", "POST"}:
        return response_payload(405, {"error": "method_not_allowed", "status": 405})

    cron_error = require_cron_secret(metadata)
    if cron_error:
        return cron_error

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

    try:
        rules, rules_error = integrity.load_rules(), None
    except integrity.RulesError as exc:
        rules, rules_error = None, str(exc)

    date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
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

        integrity_info = _run_integrity(owner, date, recipients, rules, rules_error)
        integrity_result = {k: v for k, v in integrity_info.items()
                            if k not in {"digest_lines", "digest_ids"}}

        try:
            rows = _fetch_session_rows(owner, date)
            notes = _fetch_study_notes(owner, date)
        except ReportDataError as exc:
            sent, detail = _send_email(
                recipients,
                f"[OpenBrain] Study Session REPORT FAILED — {owner} — {date}",
                _build_failure_html(owner, date, str(exc)),
            )
            results.append({"owner": owner, "status": "data_error", "error": str(exc),
                            "failure_email": "sent" if sent else detail,
                            "integrity": integrity_result})
            continue

        if rows or notes:
            html = _build_report_html(owner, date, rows, notes, integrity_info)
            subject = f"[OpenBrain] Study Session — {owner} — {date}"
        else:
            html = _build_heartbeat_html(owner, date, integrity_info)
            subject = f"[OpenBrain] Study Session — {owner} — {date} — no activity"
        sent, detail = _send_email(recipients, subject, html)

        if sent and integrity_info.get("digest_ids"):
            try:
                integrity.mark_reported(integrity_info["digest_ids"])
            except Exception as exc:
                integrity_result["mark_error"] = type(exc).__name__

        results.append({
            "owner": owner,
            "status": "sent" if sent else "email_failed",
            "query_count": len(rows),
            "note_count": len(notes),
            "recipients": recipients,
            "resend_id": detail if sent else None,
            "error": None if sent else detail,
            "integrity": integrity_result,
        })

    # Retention runs whether or not the rules loaded: expiry is a privacy promise.
    try:
        purged: int | str = integrity.purge_expired()
    except Exception as exc:
        purged = f"error ({type(exc).__name__})"

    return response_payload(200, {
        "date": date,
        "results": results,
        "integrity_rules": "loaded" if rules is not None else f"NOT LOADED: {rules_error}",
        "integrity_purged": purged,
    })
