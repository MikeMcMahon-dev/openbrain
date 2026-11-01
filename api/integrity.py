"""Tutoring-integrity trip-wire: detection, self-report validation, storage, parent email.

What this can and cannot see. It reads only what reaches OpenBrain: the tool calls a Custom
GPT chooses to make (public.query_log) and events the GPT reports about itself. It never sees
the chat. A reply that makes no tool call, or work done outside the GPT, leaves nothing here.
Every parent-facing output says so; "nothing flagged" never means "nothing happened".

Two evidence sources, kept separate on every row:
  * server_heuristic: deterministic rules over query_log, run by the nightly cron. The
    student cannot influence it except by avoiding tool calls.
  * gpt_self_report: POST /openbrain_integrity_event. Low trust: nothing forces the GPT to
    call it. Priority and confidence are assigned here, never taken from the request.

The rules (match patterns, thresholds, which events go to the separate parent email, the
monitored owners) are NOT in this repo. They live in the INTEGRITY_RULES env var, whose source
copy is kept privately, so the monitored student cannot read the thresholds and tune around
them. With no valid rules, nothing is detected or accepted, and the nightly report says
"NOT running". It must never read as a clean day.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from psycopg import connect
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover - psycopg is in requirements.txt
    connect = None
    dict_row = None

EVENT_TYPES = (
    "answer_request",
    "repeated_bypass",
    "possible_answer_disclosure",
    "worksheet_completion",
    "unsupported_confirmation",
)
SOURCES = ("server_heuristic", "gpt_self_report")
LEVELS = ("low", "medium", "high")

# Server-assigned. A request cannot raise or lower these.
PRIORITY = {
    "answer_request": "low",
    "repeated_bypass": "medium",
    "possible_answer_disclosure": "high",
    "worksheet_completion": "high",
    "unsupported_confirmation": "low",
}
SELF_REPORT_CONFIDENCE = {
    "answer_request": "medium",
    "repeated_bypass": "medium",
    "possible_answer_disclosure": "medium",
    "worksheet_completion": "medium",
    "unsupported_confirmation": "low",
}

# Who did it: a request from the student, or a slip by the tutor. Parents see this
# distinction so a model failure is never presented as the child's behaviour.
LABELS = {
    "answer_request": ("Asked for assignment answers", "student request"),
    "repeated_bypass": ("Asked again for answers after Study Buddy declined", "student request"),
    "possible_answer_disclosure": ("Study Buddy may have given away an answer", "tutor slip"),
    "worksheet_completion": ("An assignment may have been worked through for the student",
                             "tutor slip"),
    "unsupported_confirmation": ("Repeated 'is it right?' checks without a real attempt",
                                 "student request"),
}
SOURCE_LABELS = {
    "server_heuristic": "pattern in Study Buddy's note lookups",
    "gpt_self_report": "Study Buddy reported this itself",
}

COVERAGE_NOTE = (
    "Coverage: this only sees Study Buddy sessions that look things up in the notes, plus what "
    "Study Buddy reports about itself. It cannot see the chat, replies that skip the notes, or "
    "anything done outside Study Buddy."
)

RULES_ENV = "INTEGRITY_RULES"
SELF_REPORT_FIELDS = frozenset({"event_type", "reason", "subject_hint", "idempotency_key"})
SELF_REPORT_MAX_BYTES = 2048
SELF_REPORT_HOURLY_LIMIT = 20
_IDEM_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_RULE_KINDS = ("item_sequence", "any_match")


class RulesError(ValueError):
    """INTEGRITY_RULES is missing or invalid. Callers must fail closed."""


# ── rules ────────────────────────────────────────────────────────────────────────────────

def load_rules(raw: str | None = None) -> dict[str, Any]:
    """Parse and validate INTEGRITY_RULES. Raises RulesError, never returns a partial config."""
    raw = os.getenv(RULES_ENV, "") if raw is None else raw
    if not raw or not raw.strip():
        raise RulesError(f"{RULES_ENV} is not set")
    try:
        cfg = json.loads(raw)
    except Exception as exc:
        raise RulesError(f"{RULES_ENV} is not valid JSON") from exc
    if not isinstance(cfg, dict) or cfg.get("version") != 1:
        raise RulesError(f"{RULES_ENV} must be an object with version 1")

    owners = cfg.get("monitored_owners")
    if not isinstance(owners, list) or not owners or not all(
        isinstance(o, str) and o.strip() for o in owners
    ):
        raise RulesError("monitored_owners must be a non-empty list of owner strings")

    gap = cfg.get("session_gap_minutes", 30)
    if not isinstance(gap, int) or not 1 <= gap <= 240:
        raise RulesError("session_gap_minutes must be an integer 1..240")

    rules = cfg.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RulesError("rules must be a non-empty list")
    compiled = []
    seen_ids = set()
    for r in rules:
        if not isinstance(r, dict):
            raise RulesError("each rule must be an object")
        rid = r.get("id")
        if not isinstance(rid, str) or not rid or len(rid) > 64 or rid in seen_ids:
            raise RulesError(f"rule id missing, too long, or duplicated: {rid!r}")
        seen_ids.add(rid)
        if r.get("kind") not in _RULE_KINDS:
            raise RulesError(f"rule {rid}: kind must be one of {_RULE_KINDS}")
        if r.get("event_type") not in EVENT_TYPES:
            raise RulesError(f"rule {rid}: unknown event_type")
        if r.get("confidence") not in LEVELS:
            raise RulesError(f"rule {rid}: confidence must be one of {LEVELS}")
        if not isinstance(r.get("reason"), str) or not r["reason"].strip():
            raise RulesError(f"rule {rid}: reason template required")
        try:
            pattern = re.compile(r.get("pattern") or "", re.IGNORECASE)
        except re.error as exc:
            raise RulesError(f"rule {rid}: pattern does not compile") from exc
        if not r.get("pattern"):
            raise RulesError(f"rule {rid}: pattern required")
        threshold_key = "min_distinct_items" if r["kind"] == "item_sequence" else "min_matches"
        threshold = r.get(threshold_key)
        if not isinstance(threshold, int) or threshold < 1:
            raise RulesError(f"rule {rid}: {threshold_key} must be a positive integer")
        if r["kind"] == "item_sequence" and pattern.groups < 1:
            raise RulesError(f"rule {rid}: item_sequence pattern needs a capture group")
        compiled.append({**r, "_re": pattern, "_threshold": threshold})

    review = cfg.get("review")
    if not isinstance(review, list):
        raise RulesError("review must be a list of 'event_type:source' entries")
    for entry in review:
        etype, _, source = str(entry).partition(":")
        if etype not in EVENT_TYPES or source not in (*SOURCES, "*"):
            raise RulesError(f"review entry not recognised: {entry!r}")

    tz = cfg.get("display_timezone", "UTC")
    if not isinstance(tz, str):
        raise RulesError("display_timezone must be a string")
    names = cfg.get("display_names", {})
    if not isinstance(names, dict) or not all(isinstance(v, str) for v in names.values()):
        raise RulesError("display_names must map owner -> name")

    return {
        "monitored_owners": [o.strip() for o in owners],
        "session_gap_minutes": gap,
        "rules": compiled,
        "review": set(review),
        "display_timezone": tz,
        "display_names": names,
    }


def is_monitored(owner: str | None, rules: Mapping[str, Any]) -> bool:
    return bool(owner) and owner in rules["monitored_owners"]


def needs_review(event: Mapping[str, Any], rules: Mapping[str, Any]) -> bool:
    """True if the event goes in the separate parent email, False if it is a usage-report line."""
    etype, source = event.get("event_type"), event.get("evidence_source")
    return f"{etype}:{source}" in rules["review"] or f"{etype}:*" in rules["review"]


# ── server heuristic ─────────────────────────────────────────────────────────────────────

def _as_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def split_sessions(rows: Iterable[Mapping[str, Any]], gap_minutes: int) -> list[list[dict]]:
    """Group query rows into sessions: a gap longer than gap_minutes starts a new one."""
    timed = []
    for r in rows:
        ts = _as_dt(r.get("created_at"))
        if ts is not None:
            timed.append({**r, "created_at": ts})
    timed.sort(key=lambda r: r["created_at"])
    sessions: list[list[dict]] = []
    gap = timedelta(minutes=gap_minutes)
    for r in timed:
        if sessions and r["created_at"] - sessions[-1][-1]["created_at"] <= gap:
            sessions[-1].append(r)
        else:
            sessions.append([r])
    return sessions


def _heuristic_key(owner: str, rule_id: str, session_start: datetime) -> str:
    # Keyed on the session's first lookup, so re-scanning an overlapping window (the cron
    # looks back past midnight UTC) finds the same session and inserts nothing new.
    raw = f"{owner}|{rule_id}|{session_start.isoformat()}"
    return "h:" + hashlib.sha256(raw.encode()).hexdigest()[:40]


def detect(
    rows: Iterable[Mapping[str, Any]],
    owner: str,
    tenant_id: str,
    rules: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Run every rule over every session. Pure: no DB, no clock."""
    events: list[dict[str, Any]] = []
    for session in split_sessions(rows, rules["session_gap_minutes"]):
        start = session[0]["created_at"]
        for rule in rules["rules"]:
            matched: list[dict] = []
            items: list[str] = []
            for row in session:
                text = row.get("query_text") or ""
                if rule["kind"] == "item_sequence":
                    found = [m.group(1) for m in rule["_re"].finditer(text)]
                    if found:
                        matched.append(row)
                        items.extend(found)
                elif rule["_re"].search(text):
                    matched.append(row)
            distinct = sorted(set(items), key=lambda s: (len(s), s))
            count = len(distinct) if rule["kind"] == "item_sequence" else len(matched)
            if count < rule["_threshold"]:
                continue
            shown = ", ".join(distinct[:12]) + ("…" if len(distinct) > 12 else "")
            reason = rule["reason"].format(count=count, items=shown)[:280]
            events.append({
                "owner": owner,
                "tenant_id": tenant_id,
                "event_type": rule["event_type"],
                "evidence_source": "server_heuristic",
                "priority": PRIORITY[rule["event_type"]],
                "confidence": rule["confidence"],
                "reason": reason,
                "subject_hint": None,
                "rule_id": rule["id"],
                "query_log_ids": [str(r["id"]) for r in matched if r.get("id")],
                "idempotency_key": _heuristic_key(owner, rule["id"], start),
                "occurred_at": start,
            })
    return events


# ── GPT self-report ──────────────────────────────────────────────────────────────────────

def build_self_report(
    payload: Any,
    owner: str,
    tenant_id: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Validate a self-report body into an event row. Returns (event, None) or (None, error)."""
    if not isinstance(payload, Mapping):
        return None, "Body must be a JSON object."
    unknown = set(payload) - SELF_REPORT_FIELDS
    if unknown:
        return None, f"Unknown field(s): {', '.join(sorted(map(str, unknown)))}."
    etype = payload.get("event_type")
    if etype not in EVENT_TYPES:
        return None, f"event_type must be one of: {', '.join(EVENT_TYPES)}."
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return None, "reason is required."
    reason = " ".join(reason.split())
    if len(reason) > 280:
        return None, "reason must be 280 characters or fewer."
    hint = payload.get("subject_hint")
    if hint is not None:
        if not isinstance(hint, str) or len(hint.strip()) > 80:
            return None, "subject_hint must be a string of 80 characters or fewer."
        hint = hint.strip() or None
    client_key = payload.get("idempotency_key")
    if client_key is not None and (
        not isinstance(client_key, str) or not _IDEM_RE.match(client_key)
    ):
        return None, "idempotency_key must be 1-64 characters of A-Z a-z 0-9 _ . : -"

    now = now or datetime.now(timezone.utc)
    if client_key:
        key = "c:" + client_key
    else:
        # A replay of the same report within the same 10-minute bucket collapses to one row.
        bucket = int(now.timestamp() // 600)
        raw = f"{owner}|{etype}|{reason.lower()}|{bucket}"
        key = "s:" + hashlib.sha256(raw.encode()).hexdigest()[:40]

    return {
        "owner": owner,
        "tenant_id": tenant_id,
        "event_type": etype,
        "evidence_source": "gpt_self_report",
        "priority": PRIORITY[etype],
        "confidence": SELF_REPORT_CONFIDENCE[etype],
        "reason": reason,
        "subject_hint": hint,
        "rule_id": None,
        "query_log_ids": [],
        "idempotency_key": key,
        "occurred_at": now,
    }, None


# ── storage ──────────────────────────────────────────────────────────────────────────────

def _connect():
    from api._openbrain_api import _db_conninfo

    if connect is None:
        raise RuntimeError("psycopg is not available")
    return connect(_db_conninfo(), row_factory=dict_row, autocommit=True, prepare_threshold=None)


_INSERT_SQL = """
    INSERT INTO public.integrity_events
        (owner, tenant_id, event_type, evidence_source, priority, confidence, reason,
         subject_hint, rule_id, query_log_ids, idempotency_key, occurred_at)
    VALUES (%(owner)s, %(tenant_id)s, %(event_type)s, %(evidence_source)s, %(priority)s,
            %(confidence)s, %(reason)s, %(subject_hint)s, %(rule_id)s,
            %(query_log_ids)s::uuid[], %(idempotency_key)s, %(occurred_at)s)
    ON CONFLICT (owner, idempotency_key) DO NOTHING
    RETURNING id
"""


def insert_events(events: list[dict[str, Any]], conn=None) -> list[str]:
    """Insert events; duplicates (same owner + idempotency_key) are skipped. Returns new ids."""
    if not events:
        return []
    own = conn is None
    conn = conn or _connect()
    try:
        ids = []
        for e in events:
            row = conn.execute(_INSERT_SQL, e).fetchone()
            if row:
                ids.append(str(row["id"]))
        return ids
    finally:
        if own:
            conn.close()


def count_recent_self_reports(owner: str, minutes: int = 60, conn=None) -> int:
    own = conn is None
    conn = conn or _connect()
    try:
        row = conn.execute(
            """SELECT count(*) AS n FROM public.integrity_events
               WHERE owner = %s AND evidence_source = 'gpt_self_report'
                 AND created_at > now() - make_interval(mins => %s)""",
            [owner, minutes],
        ).fetchone()
        return int(row["n"])
    finally:
        if own:
            conn.close()


def fetch_query_rows(owner: str, start: datetime, end: datetime, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or _connect()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT id, query_text, created_at FROM public.query_log
               WHERE owner = %s AND created_at >= %s AND created_at < %s
               ORDER BY created_at""",
            [owner, start, end],
        ).fetchall()]
    finally:
        if own:
            conn.close()


def fetch_unreported(owner: str, conn=None) -> list[dict]:
    own = conn is None
    conn = conn or _connect()
    try:
        return [dict(r) for r in conn.execute(
            """SELECT id, event_type, evidence_source, priority, confidence, reason,
                      subject_hint, occurred_at
               FROM public.integrity_events
               WHERE owner = %s AND reported_at IS NULL AND expires_at > now()
               ORDER BY occurred_at""",
            [owner],
        ).fetchall()]
    finally:
        if own:
            conn.close()


def mark_reported(ids: list[str], conn=None) -> int:
    if not ids:
        return 0
    own = conn is None
    conn = conn or _connect()
    try:
        cur = conn.execute(
            "UPDATE public.integrity_events SET reported_at = now() WHERE id = ANY(%s::uuid[])",
            [ids],
        )
        return cur.rowcount
    finally:
        if own:
            conn.close()


def purge_expired(conn=None) -> int:
    own = conn is None
    conn = conn or _connect()
    try:
        sql = "DELETE FROM public.integrity_events WHERE expires_at <= now()"
        return conn.execute(sql).rowcount
    finally:
        if own:
            conn.close()


# ── parent-facing output ─────────────────────────────────────────────────────────────────

def _local(ts: Any, tz_name: str) -> str:
    dt = _as_dt(ts)
    if dt is None:
        return ""
    try:
        from zoneinfo import ZoneInfo

        return dt.astimezone(ZoneInfo(tz_name)).strftime("%a %b %d, %-I:%M %p")
    except Exception:
        return dt.strftime("%a %b %d, %H:%M UTC")


def event_line(event: Mapping[str, Any], tz_name: str) -> str:
    """One plain-text line per event. Used for low-priority items in the usage report."""
    label, _ = LABELS.get(event.get("event_type"), (event.get("event_type"), ""))
    return f"{_local(event.get('occurred_at'), tz_name)}: {label}. {event.get('reason', '')}"


def build_flag_email_html(display_name: str, events: list[Mapping[str, Any]], tz_name: str) -> str:
    """The separate 'worth a conversation' email. Short reason only; no query or chat text."""
    e = html.escape
    cards = ""
    for ev in events:
        label, who = LABELS.get(ev.get("event_type"), (ev.get("event_type"), ""))
        if ev.get("evidence_source") == "server_heuristic":
            # A lookup pattern cannot say whether the student or the tutor did the work.
            who = "lookup pattern; who did the work is unclear"
        hint = ev.get("subject_hint")
        cards += f"""
        <div style="border:1px solid #ddd;border-radius:6px;padding:12px;margin-bottom:12px;">
          <div style="font-size:0.85em;color:#666;">{e(_local(ev.get('occurred_at'), tz_name))}
            {f' · {e(hint)}' if hint else ''}</div>
          <div style="font-weight:bold;margin:4px 0;">{e(label)}
            <span style="font-weight:normal;color:#666;">({e(who)})</span></div>
          <div>{e(ev.get('reason') or '')}</div>
          <div style="font-size:0.8em;color:#666;margin-top:6px;">
            Source: {e(SOURCE_LABELS.get(ev.get('evidence_source'), ''))}
            · confidence: {e(ev.get('confidence') or '')}</div>
        </div>"""
    return f"""
    <html><body style="font-family:sans-serif;max-width:640px;margin:auto;">
    <h2>Study Buddy: worth a conversation</h2>
    <p>Something in {e(display_name)}'s Study Buddy use may be worth talking about.
    <strong>This is a possible tutoring-integrity concern, not proof of cheating.</strong>
    Ask what happened before
    drawing conclusions. A "tutor slip" is Study Buddy's mistake, not the student's.</p>
    {cards}
    <p style="color:#666;font-size:0.85em">{e(COVERAGE_NOTE)}</p>
    <p style="color:#999;font-size:0.75em">Generated by OpenBrain integrity monitor.
    The full day's activity is in the separate usage report.</p>
    </body></html>
    """
