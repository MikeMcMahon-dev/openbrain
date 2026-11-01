"""Tutoring-integrity trip-wire: rules, detection, self-report endpoint, nightly cron.

The rules used here are SYNTHETIC. The production patterns and thresholds are deliberately not
in this repo (see api/integrity.py), so nothing below describes what production matches.

Coverage map to the design brief's validation cases:
  1 single answer request -> no real-time path; nightly routing follows the review policy
  2 repeated attempts -> repeated_bypass, replays dedupe
  3 tutor reveals an answer -> possible_answer_disclosure goes to the separate email
  4 uploaded worksheet -> GPT behaviour, manual test (only its self-report is testable here)
  5 legitimate concept explanation -> no heuristic event
  6 more sessions, no violations -> no event
  7 cross-tenant writes -> denied (no read route exists)
  8 forged / replayed events -> rejected / deduplicated
  9 coverage limitation -> stated in every parent-facing output; missing rules say NOT running
 10 personal content -> events and emails carry only the short reason, never query text
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import api.cron_session_report as cron
import api.integrity_event as endpoint
from api import integrity

OWNER = "student-a"
OTHER = "parent-b"
TOKENS = {"student-token": OWNER, "parent-token": OTHER}

RULES = {
    "version": 1,
    "monitored_owners": [OWNER],
    "session_gap_minutes": 30,
    "display_timezone": "UTC",
    "display_names": {OWNER: "Student A"},
    "rules": [
        {"id": "seq", "kind": "item_sequence", "event_type": "worksheet_completion",
         "confidence": "medium", "pattern": r"\bitem\s+(\d+)", "min_distinct_items": 4,
         "reason": "{count} numbered items looked up in one session ({items})"},
        {"id": "fill", "kind": "any_match", "event_type": "answer_request",
         "confidence": "low", "pattern": r"\bblank answer\b", "min_matches": 1,
         "reason": "{count} lookup(s) phrased as an answer request"},
    ],
    "review": [
        "answer_request:gpt_self_report",
        "repeated_bypass:*",
        "possible_answer_disclosure:*",
        "worksheet_completion:*",
    ],
}
T0 = datetime(2026, 10, 1, 22, 0, tzinfo=timezone.utc)


def _rules(**overrides):
    return integrity.load_rules(json.dumps({**RULES, **overrides}))


def _rows(texts, start=T0, step_minutes=2):
    return [
        {"id": f"00000000-0000-0000-0000-{i:012d}", "query_text": t,
         "created_at": start + timedelta(minutes=i * step_minutes)}
        for i, t in enumerate(texts)
    ]


# ── rules: fail closed ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "",
    "not json",
    json.dumps({**RULES, "version": 2}),
    json.dumps({**RULES, "monitored_owners": []}),
    json.dumps({**RULES, "rules": []}),
    json.dumps({**RULES, "rules": [{**RULES["rules"][0], "pattern": "(unclosed"}]}),
    json.dumps({**RULES, "rules": [{**RULES["rules"][0], "pattern": r"item \d+"}]}),
    json.dumps({**RULES, "rules": [{**RULES["rules"][0], "event_type": "cheating"}]}),
    json.dumps({**RULES, "rules": [{**RULES["rules"][0], "min_distinct_items": 0}]}),
    json.dumps({**RULES, "review": ["answer_request:gossip"]}),
])
def test_invalid_rules_raise(raw):
    with pytest.raises(integrity.RulesError):
        integrity.load_rules(raw)


def test_unset_rules_raise(monkeypatch):
    monkeypatch.delenv(integrity.RULES_ENV, raising=False)
    with pytest.raises(integrity.RulesError):
        integrity.load_rules()


# ── server heuristic ─────────────────────────────────────────────────────────────────────

def test_worksheet_sequence_flags_with_ids_and_no_query_text():
    texts = [f"item {n}: combine the terms in 4a + {n}a" for n in (3, 4, 5, 6, 7)]
    events = integrity.detect(_rows(texts), OWNER, "t", _rules())
    assert [e["event_type"] for e in events] == ["worksheet_completion"]
    ev = events[0]
    assert ev["evidence_source"] == "server_heuristic" and ev["priority"] == "high"
    assert len(ev["query_log_ids"]) == 5
    assert "combine" not in ev["reason"] and "4a" not in ev["reason"]  # case 10


def test_threshold_mutation_turns_the_flag_off():
    """The guard can go red: the same session stops flagging when the threshold is raised."""
    texts = [f"item {n}" for n in (3, 4, 5, 6, 7)]
    rules = _rules(rules=[{**RULES["rules"][0], "min_distinct_items": 99}])
    assert integrity.detect(_rows(texts), OWNER, "t", rules) == []


def test_single_item_and_concept_questions_do_not_flag():  # cases 5 and 6
    days = [
        _rows(["how do I find protons from the periodic table", "item 17 hint please"],
              start=T0 + timedelta(days=d))
        for d in range(10)
    ]
    rows = [r for day in days for r in day]
    for i, r in enumerate(rows):
        r["id"] = f"00000000-0000-0000-0000-{i:012d}"
    assert integrity.detect(rows, OWNER, "t", _rules()) == []


def test_items_split_across_sessions_do_not_combine():
    early = _rows(["item 1", "item 2", "item 3"])
    late = _rows(["item 4", "item 5", "item 6"], start=T0 + timedelta(hours=3))
    assert integrity.detect(early + late, OWNER, "t", _rules()) == []


def test_overlapping_rescan_yields_the_same_key():
    rows = _rows([f"item {n}" for n in range(1, 7)])
    first = integrity.detect(rows[:4], OWNER, "t", _rules())
    second = integrity.detect(rows, OWNER, "t", _rules())
    assert first[0]["idempotency_key"] == second[0]["idempotency_key"]


def test_answer_pattern_is_low_and_digest_only():
    events = integrity.detect(_rows(["blank answer please"]), OWNER, "t", _rules())
    assert [(e["event_type"], e["priority"]) for e in events] == [("answer_request", "low")]
    assert integrity.needs_review(events[0], _rules()) is False


# ── self-report validation (case 8) ──────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"event_type": "answer_request", "reason": "x", "priority": "low"},
    {"event_type": "answer_request", "reason": "x", "confidence": "high"},
    {"event_type": "answer_request", "reason": "x", "owner": OTHER},
    {"event_type": "cheating", "reason": "x"},
    {"event_type": "answer_request", "reason": "   "},
    {"event_type": "answer_request", "reason": "x" * 281},
    {"event_type": "answer_request", "reason": "x", "subject_hint": "y" * 81},
    {"event_type": "answer_request", "reason": "x", "idempotency_key": "bad key!"},
    ["not", "an", "object"],
])
def test_self_report_rejects_forged_or_malformed(body):
    event, problem = integrity.build_self_report(body, OWNER, "t")
    assert event is None and problem


def test_self_report_replay_dedupes_within_bucket_only():
    body = {"event_type": "repeated_bypass", "reason": "Asked again for worksheet answers"}
    a, _ = integrity.build_self_report(body, OWNER, "t", now=T0)
    b, _ = integrity.build_self_report(
        {**body, "reason": "  asked AGAIN for worksheet   answers "}, OWNER, "t",
        now=T0 + timedelta(minutes=1))
    c, _ = integrity.build_self_report(body, OWNER, "t", now=T0 + timedelta(minutes=30))
    assert a["idempotency_key"] == b["idempotency_key"] != c["idempotency_key"]
    assert a["priority"] == "medium" and a["confidence"] == "medium"


def test_review_policy_routes_single_request_by_configuration():  # case 1
    ev, _ = integrity.build_self_report(
        {"event_type": "answer_request", "reason": "Asked for answers to uploaded pages"},
        OWNER, "t")
    assert integrity.needs_review(ev, _rules()) is True
    brief_default = _rules(review=["repeated_bypass:*", "possible_answer_disclosure:*",
                                   "worksheet_completion:*"])
    assert integrity.needs_review(ev, brief_default) is False


# ── parent email (cases 3, 9, 10) ────────────────────────────────────────────────────────

def test_flag_email_is_non_accusatory_escaped_and_states_coverage():
    ev, _ = integrity.build_self_report(
        {"event_type": "possible_answer_disclosure", "reason": "Gave <b>7</b> for #3"},
        OWNER, "t", now=T0)
    page = integrity.build_flag_email_html("Student A", [ev], "UTC")
    assert "not proof of cheating" in page
    assert "tutor slip" in page
    assert integrity.COVERAGE_NOTE in page
    assert "<b>7</b>" not in page and "&lt;b&gt;7&lt;/b&gt;" in page


# ── endpoint ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def stub_store(monkeypatch):
    calls = {"inserted": [], "recent": 0, "insert_result": ["11111111-1111-1111-1111-111111111111"]}

    def _insert(events, conn=None):
        calls["inserted"].extend(events)
        return calls["insert_result"]

    monkeypatch.setattr(integrity, "insert_events", _insert)
    monkeypatch.setattr(integrity, "count_recent_self_reports",
                        lambda owner, minutes=60, conn=None: calls["recent"])
    monkeypatch.setenv("OPENBRAIN_TOKEN_OWNER_MAP", json.dumps(TOKENS))
    monkeypatch.setenv(integrity.RULES_ENV, json.dumps(RULES))
    monkeypatch.delenv("OPENBRAIN_TOOL_ACCESS_TOKEN", raising=False)
    return calls


def _post(body, token="student-token", extra_headers=None, method="POST"):
    headers = {"authorization": f"Bearer {token}"} if token else {}
    headers.update(extra_headers or {})
    return endpoint.handler({"method": method, "headers": headers, "body": json.dumps(body)})


GOOD = {"event_type": "answer_request", "reason": "Asked for answers to uploaded math pages"}


def _status(resp):
    return resp["statusCode"], json.loads(resp["body"]).get("status")


def test_endpoint_logs_with_token_owner_despite_spoofed_header(stub_store):  # case 7
    resp = _post(GOOD, extra_headers={"x-openbrain-owner": OTHER, "x-openbrain-tenant-id": "evil"})
    assert _status(resp) == (201, "logged")
    assert stub_store["inserted"][0]["owner"] == OWNER
    assert stub_store["inserted"][0]["tenant_id"] != "evil"


def test_endpoint_accepts_tool_input_envelope(stub_store):
    assert _status(_post({"tool_input": GOOD}))[0] == 201


@pytest.mark.parametrize("token,code", [("parent-token", 403), ("forged", 401), (None, 401)])
def test_endpoint_refuses_other_owners_and_bad_tokens(stub_store, token, code):
    assert _post(GOOD, token=token)["statusCode"] == code
    assert stub_store["inserted"] == []


def test_endpoint_refuses_shared_token_and_no_auth_config(stub_store, monkeypatch):
    monkeypatch.setenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "shared")
    assert _post(GOOD, token="shared")["statusCode"] == 403
    monkeypatch.delenv("OPENBRAIN_TOOL_ACCESS_TOKEN")
    monkeypatch.delenv("OPENBRAIN_TOKEN_OWNER_MAP")
    assert _post(GOOD, token=None)["statusCode"] == 403  # dev pass-through has no owner
    assert stub_store["inserted"] == []


def test_endpoint_fails_closed_without_rules(stub_store, monkeypatch):
    monkeypatch.delenv(integrity.RULES_ENV)
    assert _status(_post(GOOD)) == (503, "not_logged")


def test_endpoint_rejects_forged_severity_and_oversize(stub_store):
    assert _post({**GOOD, "priority": "low"})["statusCode"] == 400
    assert _post({**GOOD, "subject_hint": "z" * 3000})["statusCode"] == 413
    assert stub_store["inserted"] == []


def test_endpoint_duplicate_rate_limit_storage_and_method(stub_store, monkeypatch):
    stub_store["insert_result"] = []
    assert _status(_post(GOOD)) == (200, "duplicate")
    stub_store["recent"] = integrity.SELF_REPORT_HOURLY_LIMIT
    assert _post(GOOD)["statusCode"] == 429
    stub_store["recent"] = 0

    def _boom(events, conn=None):
        raise RuntimeError("db down")

    monkeypatch.setattr(integrity, "insert_events", _boom)
    assert _status(_post(GOOD)) == (500, "not_logged")
    assert _post(GOOD, method="GET")["statusCode"] == 405


# ── nightly cron ─────────────────────────────────────────────────────────────────────────

@pytest.fixture
def cron_env(monkeypatch):
    sent, marked = [], []
    state = {"pending": [], "rows": [{"query_text": "q", "result_count": 1, "mode": "explain",
                                      "flagged": False, "created_at": T0}],
             "send_ok": True, "data_error": False, "purged": 0}
    monkeypatch.setenv("CRON_SECRET", "s3cret")
    monkeypatch.setenv("REPORT_CONFIGS", json.dumps([{"owner": OWNER, "recipients": ["p@x"]}]))
    monkeypatch.setenv(integrity.RULES_ENV, json.dumps(RULES))

    def _send(recipients, subject, html):
        sent.append((subject, html))
        return state["send_ok"], "id-1" if state["send_ok"] else "boom"

    def _fetch_rows(owner, date):
        if state["data_error"]:
            raise cron.ReportDataError("query_log read failed (OperationalError)")
        return state["rows"]

    def _purge(conn=None):
        state["purged"] += 1
        return 0

    monkeypatch.setattr(cron, "_send_email", _send)
    monkeypatch.setattr(cron, "_fetch_session_rows", _fetch_rows)
    monkeypatch.setattr(cron, "_fetch_study_notes", lambda owner, date: [])
    monkeypatch.setattr(integrity, "fetch_query_rows", lambda *a, **k: [])
    monkeypatch.setattr(integrity, "insert_events", lambda events, conn=None: [])
    monkeypatch.setattr(integrity, "fetch_unreported", lambda owner, conn=None: state["pending"])
    monkeypatch.setattr(integrity, "mark_reported",
                        lambda ids, conn=None: marked.extend(ids) or len(ids))
    monkeypatch.setattr(integrity, "purge_expired", _purge)
    return sent, marked, state


def _run(secret="s3cret"):
    headers = {"authorization": f"Bearer {secret}"} if secret else {}
    resp = cron.handler({"method": "GET", "headers": headers})
    return resp["statusCode"], json.loads(resp["body"])


def _event(etype, source, eid):
    return {"id": eid, "event_type": etype, "evidence_source": source, "priority": "x",
            "confidence": "medium", "reason": f"{etype} reason", "subject_hint": None,
            "occurred_at": T0}


def test_cron_fails_closed_without_or_with_wrong_secret(cron_env, monkeypatch):
    assert _run("wrong")[0] == 401
    monkeypatch.delenv("CRON_SECRET")
    assert _run("anything")[0] == 500
    assert cron_env[0] == []


def test_cron_sends_flag_email_separately_and_marks_after_send(cron_env):
    sent, marked, state = cron_env
    state["pending"] = [_event("possible_answer_disclosure", "gpt_self_report", "r1"),
                        _event("unsupported_confirmation", "gpt_self_report", "d1")]
    assert _run()[0] == 200
    subjects = [s for s, _ in sent]
    assert any("worth a conversation" in s for s in subjects)
    usage = next(h for s, h in sent if "worth a conversation" not in s)
    assert "1 item(s) sent in a separate" in usage
    assert "unsupported_confirmation reason" in usage  # digest line in the usage report
    assert "possible_answer_disclosure reason" not in usage
    assert marked == ["r1", "d1"]


def test_cron_failed_flag_email_is_not_marked_and_is_reported(cron_env):
    sent, marked, state = cron_env
    state["pending"] = [_event("worksheet_completion", "server_heuristic", "r1")]
    state["send_ok"] = False
    _run()
    assert marked == []
    assert "FAILED to send" in sent[-1][1]


def test_cron_without_rules_says_not_running_and_still_purges(cron_env, monkeypatch):
    sent, _, state = cron_env
    monkeypatch.delenv(integrity.RULES_ENV)
    code, body = _run()
    assert code == 200 and body["integrity_rules"].startswith("NOT LOADED")
    assert "Integrity check NOT running" in sent[-1][1]
    assert state["purged"] == 1


def test_cron_heartbeat_on_no_activity(cron_env):
    sent, _, state = cron_env
    state["rows"] = []
    _run()
    subject, page = sent[-1]
    assert subject.endswith("no activity")
    assert "Integrity check ran" in page and integrity.COVERAGE_NOTE in page


def test_cron_data_error_sends_failure_not_silence(cron_env):
    sent, _, state = cron_env
    state["data_error"] = True
    code, body = _run()
    assert body["results"][0]["status"] == "data_error"
    assert "REPORT FAILED" in sent[-1][0]


@pytest.mark.parametrize("fetch", ["_fetch_session_rows", "_fetch_study_notes"])
def test_report_fetch_raises_on_db_failure_instead_of_returning_empty(monkeypatch, fetch):
    import api.session_report as report

    def _down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(report, "connect", _down)
    with pytest.raises(report.ReportDataError):
        getattr(report, fetch)(OWNER, "2026-10-01")


def test_heuristic_events_do_not_assign_blame():
    ev = integrity.detect(_rows([f"item {n}" for n in range(1, 6)]), OWNER, "t", _rules())[0]
    page = integrity.build_flag_email_html("Student A", [ev], "UTC")
    assert "who did the work is unclear" in page
    assert "(tutor slip)" not in page and "(student request)" not in page
