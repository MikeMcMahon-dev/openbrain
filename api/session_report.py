from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from api._openbrain_api import (
    _db_conninfo,
    parse_request,
    require_auth_owner,
    response_payload,
)

try:
    from psycopg import connect
    from psycopg.rows import dict_row
except Exception:
    connect = None
    dict_row = None


def _fetch_session_rows(owner: str, date: str) -> list[dict[str, Any]]:
    """Pull query_log rows for owner on a given date (YYYY-MM-DD)."""
    if connect is None:
        return []
    try:
        with connect(_db_conninfo(), row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT query_text, result_count, mode, flagged, flag_reason, created_at
                FROM public.query_log
                WHERE owner = %s
                  AND created_at::date = %s::date
                ORDER BY created_at ASC
                """,
                [owner, date],
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _fetch_study_notes(owner: str, date: str) -> list[dict[str, Any]]:
    """Pull thoughts rows for owner on a given date (YYYY-MM-DD)."""
    if connect is None:
        return []
    try:
        with connect(_db_conninfo(), row_factory=dict_row) as conn:
            rows = conn.execute(
                """
                SELECT
                    content,
                    metadata->>'subject' AS subject,
                    metadata->>'topic'   AS topic,
                    created_at
                FROM public.thoughts
                WHERE LOWER(COALESCE(created_by_user_login, metadata->>'owner', '')) = LOWER(%s)
                  AND created_at::date = %s::date
                ORDER BY created_at ASC
                """,
                [owner, date],
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _build_report_html(
    owner: str,
    date: str,
    rows: list[dict[str, Any]],
    notes: list[dict[str, Any]] | None = None,
) -> str:
    if notes is None:
        notes = []

    total = len(rows)
    flagged = [r for r in rows if r.get("flagged")]
    modes = {}
    for r in rows:
        m = r.get("mode") or "unknown"
        modes[m] = modes.get(m, 0) + 1

    # Session grouping: gaps > 30 min = new session
    sessions = 0
    last_ts = None
    for r in rows:
        ts = r.get("created_at")
        if ts is None:
            continue
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts)
            except Exception:
                continue
        if last_ts is None or (ts - last_ts).total_seconds() > 1800:
            sessions += 1
        last_ts = ts

    mode_summary = ", ".join(f"{m}: {c}" for m, c in sorted(modes.items()))

    # Study notes section
    notes_section = ""
    if notes:
        cards = ""
        for n in notes:
            subject = n.get("subject") or ""
            topic = n.get("topic") or ""
            ts = str(n.get("created_at", ""))[:19]
            content = (n.get("content") or "").replace("&", "&amp;").replace("<", "&lt;")
            label = " · ".join(x for x in [subject, topic] if x)
            cards += f"""
            <div style="border:1px solid #ddd;border-radius:4px;padding:12px;margin-bottom:12px;">
              <div style="font-size:0.8em;color:#666;margin-bottom:6px;">{ts} UTC
                {f'&nbsp;·&nbsp;<strong>{label}</strong>' if label else ''}</div>
              <pre style="white-space:pre-wrap;font-family:sans-serif;margin:0;font-size:0.9em"
              >{content}</pre>
            </div>"""
        notes_section = f"<h3>Study Session Notes ({len(notes)})</h3>{cards}"

    flag_section = ""
    if flagged:
        items = "".join(
            f"<li>{r.get('created_at', '')} — <code>{r.get('query_text', '')[:120]}</code>"
            f"<br><small>{r.get('flag_reason', '')}</small></li>"
            for r in flagged
        )
        flag_section = f"""
        <h3 style="color:#c0392b;">⚠ Flagged Queries ({len(flagged)})</h3>
        <ul>{items}</ul>
        """

    query_section = ""
    if rows:
        query_list = "".join(
            f"<li style='color:{'#c0392b' if r.get('flagged') else '#333'}'>"
            f"{str(r.get('created_at',''))[:19]} | {r.get('mode','?')} | "
            f"{r.get('result_count',0)} results — {(r.get('query_text') or '')[:100]}"
            f"{'  ⚠' if r.get('flagged') else ''}</li>"
            for r in rows
        )
        query_section = f"""
        {flag_section}
        <h3>Brain Queries ({total})</h3>
        <ul style="font-size:0.85em">{query_list}</ul>"""

    return f"""
    <html><body style="font-family:sans-serif;max-width:700px;margin:auto;">
    <h2>Study Session Report — {owner} — {date}</h2>
    <table style="border-collapse:collapse;width:100%">
      <tr><td style="padding:6px;border:1px solid #ddd"><strong>Study notes</strong></td>
          <td style="padding:6px;border:1px solid #ddd">{len(notes)}</td></tr>
      <tr><td style="padding:6px;border:1px solid #ddd"><strong>Brain queries</strong></td>
          <td style="padding:6px;border:1px solid #ddd">{total}</td></tr>
      <tr><td style="padding:6px;border:1px solid #ddd"><strong>Query sessions</strong></td>
          <td style="padding:6px;border:1px solid #ddd">{sessions}</td></tr>
      <tr><td style="padding:6px;border:1px solid #ddd"><strong>Modes</strong></td>
          <td style="padding:6px;border:1px solid #ddd">{mode_summary or 'n/a'}</td></tr>
      <tr><td style="padding:6px;border:1px solid #ddd"><strong>Flagged</strong></td>
          <td style="padding:6px;border:1px solid #ddd">{len(flagged)}</td></tr>
    </table>
    {notes_section}
    {query_section}
    <p style="color:#999;font-size:0.75em">Generated by OpenBrain session reporter</p>
    </body></html>
    """


def _send_email(
    recipients: list[str],
    subject: str,
    html: str,
) -> tuple[bool, str]:
    api_key = os.getenv("RESEND_API_KEY")
    from_address = os.getenv("RESEND_FROM_EMAIL", "reports@openbrain.app")
    if not api_key:
        return False, "RESEND_API_KEY not configured"

    payload = json.dumps({
        "from": from_address,
        "to": recipients,
        "subject": subject,
        "html": html,
    }).encode()

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read().decode())
        return True, body.get("id", "sent")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        return False, f"Resend error {exc.code}: {detail}"
    except Exception as exc:
        return False, str(exc)


def handler(request) -> dict[str, Any]:
    payload, metadata = parse_request(request)

    if metadata["method"] == "OPTIONS":
        return response_payload(200, {"ok": True})

    if metadata["method"] != "POST":
        return response_payload(405, {"error": "method_not_allowed", "status": 405})

    auth_error, token_owner = require_auth_owner(metadata)
    if auth_error:
        return auth_error

    if not isinstance(payload, Mapping):
        return response_payload(400, {"error": "validation_error", "message": "Malformed JSON payload.", "status": 400})

    owner = (payload.get("owner") or "").strip()
    date = (payload.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")).strip()
    recipients = payload.get("recipients") or []
    report_type = payload.get("report_type", "study_session")

    if not owner:
        return response_payload(400, {"error": "validation_error", "message": "owner is required.", "status": 400})
    if not isinstance(recipients, list) or not recipients:
        return response_payload(400, {
            "error": "validation_error",
            "message": "recipients must be a non-empty list.",
            "status": 400,
        })

    # If the token resolves to a specific owner, enforce it — prevent cross-tenant reads.
    if token_owner and owner != token_owner:
        return response_payload(403, {
            "error": "forbidden",
            "message": "Token is not authorized for this owner.",
            "status": 403,
        })

    rows = _fetch_session_rows(owner, date)
    notes = _fetch_study_notes(owner, date)
    if not rows and not notes:
        return response_payload(200, {
            "status": "skipped",
            "message": f"No activity for {owner} on {date}.",
            "owner": owner,
            "date": date,
            "query_count": 0,
            "note_count": 0,
        })

    html = _build_report_html(owner, date, rows, notes)
    subject = f"[OpenBrain] {report_type.replace('_', ' ').title()} — {owner} — {date}"
    sent, detail = _send_email(recipients, subject, html)

    return response_payload(200 if sent else 500, {
        "status": "sent" if sent else "email_failed",
        "owner": owner,
        "date": date,
        "query_count": len(rows),
        "note_count": len(notes),
        "recipients": recipients,
        "resend_id": detail if sent else None,
        "error": None if sent else detail,
    })
