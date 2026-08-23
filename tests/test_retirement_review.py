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
