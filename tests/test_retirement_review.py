"""Dispatch and failure handling in scripts/retirement_review.py.

The airlock shipped with 0 rows and its `delete` path had never been run. When it finally was, it
raised ForeignKeyViolation from the request table's own FK to its target, escaped as a traceback,
and left the request 'approved' — so every later run retried it and failed identically. These
tests pin the two halves that broke: which statements each method issues, and that a raising
_perform is recorded rather than propagated.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "retirement_review.py"
_SPEC = importlib.util.spec_from_file_location("retirement_review", _PATH)
rr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rr)

REQ = {"id": "req-1", "target_id": "tgt-1", "method": "retire", "reason_code": "manual"}


def test_retire_appends_an_expiry_event_and_deletes_nothing():
    c = MagicMock()
    detail = rr._perform(c, dict(REQ))
    sql = " ".join(call.args[0] for call in c.execute.call_args_list)
    assert "INSERT INTO public.supersession_events" in sql
    assert "DELETE" not in sql, "a retire must never delete — content stays reachable via as_of"
    assert "historical" in detail


def test_delete_removes_chunks_before_the_row():
    c = MagicMock()
    c.execute.return_value.rowcount = 1
    detail = rr._perform(c, dict(REQ, method="delete"))
    stmts = [call.args[0] for call in c.execute.call_args_list]
    assert "knowledge_chunked" in stmts[0], "chunks first, so no orphans if the row delete fails"
    assert "DELETE FROM public.knowledge WHERE id" in stmts[1]
    assert "INSERT INTO public.supersession_events" not in " ".join(stmts), (
        "a delete must NOT append an event — that would pin the row it is removing")
    assert detail == "deleted (1 row, 1 chunks)"


def test_perform_raises_rather_than_swallowing():
    """cmd_execute is what records a failure; _perform must let the error reach it."""
    c = MagicMock()
    c.execute.side_effect = RuntimeError("foreign key violation")
    try:
        rr._perform(c, dict(REQ, method="delete"))
    except RuntimeError:
        return
    raise AssertionError("_perform swallowed the error; cmd_execute could not mark it failed")


# ── review: the one-command interactive flow ─────────────────────────────────────────────────
# Each test pins a way the flow could do harm: writing before the final confirm, executing an
# item that was skipped, treating a closed stdin as consent, overwriting a request decided
# elsewhere, or running unattended.

from contextlib import contextmanager  # noqa: E402
from datetime import datetime  # noqa: E402


def _pending(rid, method="retire"):
    return {"id": rid, "target_id": f"tgt-{rid}", "method": method, "reason_code": "manual",
            "rationale": "because", "requested_by": "claude", "requested_at": datetime(2026, 9, 24),
            "evidence": {}, "content": f"content of {rid}", "title": f"title {rid}",
            "target_status": "current"}


class _Cur(list):
    rowcount = 0


class _FakeConn:
    def __init__(self, pending, still_pending=None):
        self.pending = pending
        self.still_pending = set(still_pending if still_pending is not None
                                 else [p["id"] for p in pending])
        self.writes: list[tuple[str, list]] = []
        self.commits = 0

    def execute(self, sql, params=None):
        if "WHERE r.status = 'pending'" in sql:
            return _Cur(self.pending)
        if sql.lstrip().startswith("UPDATE"):
            self.writes.append((sql, params))
            cur = _Cur()
            cur.rowcount = 1 if params[3] in self.still_pending else 0
            return cur
        if "ANY(" in sql:
            ids = params[0]
            return _Cur({"id": i, "target_id": f"tgt-{i}", "method": "retire",
                         "reason_code": "manual"} for i in ids)
        raise AssertionError(f"unexpected SQL: {sql[:60]}")

    def commit(self):
        self.commits += 1


def _run_review(conn, answers, tty=True):
    ran: list = []
    feed = iter(answers)

    def read(_prompt=""):
        try:
            return next(feed)
        except StopIteration:
            raise EOFError

    @contextmanager
    def fake_conn():
        yield conn

    orig_conn, orig_run = rr._conn, rr._run_approved
    rr._conn = fake_conn
    rr._run_approved = lambda c, rows: (ran.extend(r["id"] for r in rows), 0)[1]
    try:
        rc = rr.cmd_review(None, read=read, isatty=lambda: tty)
    finally:
        rr._conn, rr._run_approved = orig_conn, orig_run
    return rc, ran


def test_review_refuses_without_a_terminal_and_touches_nothing():
    conn = _FakeConn([_pending("a")])
    rc, ran = _run_review(conn, [], tty=False)
    assert rc == 2 and not conn.writes and not ran


def test_review_answer_no_writes_nothing():
    conn = _FakeConn([_pending("a"), _pending("b")])
    rc, ran = _run_review(conn, ["a", "", "d", "", "n"])
    assert not conn.writes, "decisions were written before the final confirm"
    assert not ran and rc == 1


def test_review_executes_only_what_was_approved_in_this_pass():
    conn = _FakeConn([_pending("a"), _pending("b", "delete"), _pending("c")])
    rc, ran = _run_review(conn, ["a", "note a", "s", "d", "", "y"])
    decided = {p[3]: p[0] for _, p in conn.writes}
    assert decided == {"a": "approved", "c": "denied"}, "skipped item b must stay pending"
    assert ran == ["a"], "only this pass's approvals execute; a denial or skip never does"
    assert rc == 0


def test_review_record_only_does_not_execute():
    conn = _FakeConn([_pending("a")])
    rc, ran = _run_review(conn, ["a", "", "r"])
    assert [p[3] for _, p in conn.writes] == ["a"] and not ran and rc == 0


def test_review_eof_mid_queue_is_a_quit_not_a_decision():
    conn = _FakeConn([_pending("a"), _pending("b")])
    rc, ran = _run_review(conn, ["a"])          # stdin closes at the note prompt
    assert not conn.writes and not ran
    conn = _FakeConn([_pending("a")])
    rc, ran = _run_review(conn, ["a", ""])      # stdin closes at the final confirm
    assert not conn.writes and not ran, "EOF at the confirm must mean no, never yes"


def test_review_does_not_execute_a_request_decided_elsewhere_meanwhile():
    conn = _FakeConn([_pending("a"), _pending("b")], still_pending=["b"])
    rc, ran = _run_review(conn, ["a", "", "a", "", "y"])
    assert ran == ["b"], "a was decided by someone else after listing; it must not run"


def test_review_view_then_decide():
    conn = _FakeConn([_pending("a")])
    rc, ran = _run_review(conn, ["v", "x", "a", "", "y"])   # view, invalid key, then approve
    assert ran == ["a"]
