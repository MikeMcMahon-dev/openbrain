"""ADR-018 P3/P4 — supersession + contradiction lifecycle, against the REAL deployed schema.

Ephemeral boundary = a rolled-back transaction. Every fixture row (including the deliberately
drifted / contradictory ones the handoff calls F2/F7/F10) lives only inside a transaction that is
ALWAYS rolled back in teardown — so a missed teardown is impossible (unlike a named schema, where
a crash can leak a DROP), and uncommitted rows are invisible to any other connection. Fixtures are
further isolated onto a throwaway `system='citest'` (seeded into system_vocabulary in-txn) so they
never pair with real corpus rows. The suite runs the ACTUAL prod triggers/functions/migrations —
not copies — so it cannot drift from what is deployed.

Skips (not fails) when no DB is configured, so it is safe to run anywhere; the real proof runs in
the dev/CI env with credentials. Run: `make test` or `cd tests && python -m pytest
test_supersession_lifecycle.py -v`.
"""
from __future__ import annotations

import os

import pytest

from api.contradiction_detect import confirm, detect_candidates, dismiss, list_pending
from api.supersession_reconcile import reconcile_supersession


def _has_db() -> bool:
    return bool(os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL"))


pytestmark = pytest.mark.skipif(not _has_db(), reason="no SUPABASE_DB_URL configured")

_VEC = "[" + ",".join(["0.1"] * 1536) + "]"          # identical embeddings -> cosine sim 1.0
_VEC2 = "[" + ",".join(["0.11"] * 1536) + "]"        # a second, still-similar vector


@pytest.fixture()
def tx():
    """A rolled-back psycopg connection with a throwaway system seeded. Never commits."""
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.autocommit = False
    conn.execute("INSERT INTO public.system_vocabulary (system) VALUES ('citest') "
                 "ON CONFLICT DO NOTHING")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _mk(conn, *, status="current", component_key=None, embedding=None, content="citest row"):
    """Insert a minimal valid knowledge fixture on system='citest'; return its id."""
    row = conn.execute(
        """
        INSERT INTO public.knowledge
            (content, embedding, domain, environment, system, tags, status, component_key, source)
        VALUES (%s, %s, 'OpenBrain', 'Study', 'citest', '{}', %s, %s, 'ci-test')
        RETURNING id
        """,
        [content, embedding, status, component_key],
    ).fetchone()
    return str(row["id"])


def _evt(conn, superseded, superseding=None, reason="manual", method="human"):
    """Insert a supersession event (superseding NULL = expiry)."""
    conn.execute(
        "INSERT INTO public.supersession_events "
        "(superseded_id, superseding_id, reason_code, method) VALUES (%s,%s,%s,%s)",
        [superseded, superseding, reason, method])


# ── P3: projection + guards ──────────────────────────────────────────────────────────────

def test_projection_retires_loser_on_event(tx):
    a = _mk(tx, component_key="citest-a")
    b = _mk(tx, component_key="citest-b")
    _evt(tx, a, b)
    assert tx.execute("SELECT status FROM public.knowledge WHERE id=%s", [a]).fetchone()["status"] \
        == "superseded"
    assert tx.execute("SELECT status FROM public.knowledge WHERE id=%s", [b]).fetchone()["status"] \
        == "current"


def test_expiry_event_marks_historical(tx):
    a = _mk(tx)
    _evt(tx, a, reason="ttl_expiry", method="job")
    assert tx.execute("SELECT status FROM public.knowledge WHERE id=%s", [a]).fetchone()["status"] \
        == "historical"


def test_event_log_is_append_only(tx):
    a, b = _mk(tx, component_key="x"), _mk(tx, component_key="y")
    _evt(tx, a, b)
    tx.execute("SAVEPOINT s")
    with pytest.raises(Exception, match="append-only"):
        tx.execute("UPDATE public.supersession_events SET reason_note='x' "
                   "WHERE superseded_id=%s", [a])
    tx.execute("ROLLBACK TO SAVEPOINT s")
    with pytest.raises(Exception, match="append-only"):
        tx.execute("DELETE FROM public.supersession_events WHERE superseded_id=%s", [a])


def test_deferred_fk_rejects_missing_successor_at_commit(tx):
    a = _mk(tx)
    tx.execute("SET CONSTRAINTS ALL DEFERRED")
    tx.execute("INSERT INTO public.supersession_events "
               "(superseded_id, superseding_id, reason_code, method) "
               "VALUES (%s,'00000000-0000-0000-0000-000000000000','manual','human')", [a])
    with pytest.raises(Exception, match="foreign key"):
        tx.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_one_retirement_per_document(tx):
    a, b, c = _mk(tx, component_key="x"), _mk(tx, component_key="y"), _mk(tx, component_key="z")
    _evt(tx, a, b)
    with pytest.raises(Exception, match="one_retirement_per_document"):
        _evt(tx, a, c)


# ── P3: reconciliation ───────────────────────────────────────────────────────────────────

def test_reconcile_flags_superseded_without_event(tx):
    a = _mk(tx, status="superseded")           # deliberately drifted: superseded, no event
    rep = reconcile_supersession(tx)
    hit = [d for d in rep["drift"]
           if d["knowledge_id"] == a and d["reason"] == "superseded_without_event"]
    assert len(hit) == 1
    assert rep["ok"] is False


def test_reconcile_flags_event_present_status_mismatch(tx):
    a, b = _mk(tx, component_key="x"), _mk(tx, component_key="y")
    _evt(tx, a, b)
    # projection set a->superseded; force it back to a wrong stored status
    tx.execute("SAVEPOINT s")
    tx.execute("UPDATE public.knowledge SET status='historical' WHERE id=%s", [a])
    rep = reconcile_supersession(tx)
    hit = [d for d in rep["drift"]
           if d["knowledge_id"] == a and d["reason"] == "event_present_status_mismatch"]
    assert len(hit) == 1


# ── P4: contradiction detection ──────────────────────────────────────────────────────────

def test_detect_confirm_dismiss_and_no_reflag(tx):
    # two same-system current rows with identical embeddings -> sim 1.0 -> a candidate pair
    a = _mk(tx, embedding=_VEC, content="citest contradiction A")
    b = _mk(tx, embedding=_VEC, content="citest contradiction B")
    lo, hi = sorted([a, b])

    rep = detect_candidates(tx, threshold=0.85)
    assert rep["inserted"] >= 1
    pair = next(p for p in list_pending(tx) if {p["id_lo"], p["id_hi"]} == {a, b})

    # idempotent: re-detect adds nothing for this pair
    before = tx.execute("SELECT count(*) n FROM public.contradiction_candidates").fetchone()["n"]
    detect_candidates(tx, threshold=0.85)
    after = tx.execute("SELECT count(*) n FROM public.contradiction_candidates").fetchone()["n"]
    assert before == after

    # dismiss -> no event, and re-detect does NOT re-flag
    ev_before = tx.execute("SELECT count(*) n FROM public.supersession_events").fetchone()["n"]
    assert dismiss(pair["pair_id"], "citest", "both true", conn=tx)["ok"]
    ev_after = tx.execute("SELECT count(*) n FROM public.supersession_events").fetchone()["n"]
    assert ev_after == ev_before
    detect_candidates(tx, threshold=0.85)
    status = tx.execute("SELECT status FROM public.contradiction_candidates WHERE id=%s",
                        [pair["pair_id"]]).fetchone()["status"]
    assert status == "dismissed"


def test_confirm_writes_event_and_retires_loser(tx):
    a = _mk(tx, embedding=_VEC, component_key="citest-c1", content="citest A")
    b = _mk(tx, embedding=_VEC2, component_key="citest-c2", content="citest B")
    detect_candidates(tx, threshold=0.85)
    pair = next(p for p in list_pending(tx) if {p["id_lo"], p["id_hi"]} == {a, b})

    res = confirm(pair["pair_id"], a, "citest", "confirmed contradiction", conn=tx)
    assert res["ok"] and res["winner_id"] == b
    assert tx.execute("SELECT status FROM public.knowledge WHERE id=%s", [a]).fetchone()["status"] \
        == "superseded"
    ev = tx.execute("SELECT reason_code, method FROM public.supersession_events "
                    "WHERE superseded_id=%s AND superseding_id=%s", [a, b]).fetchone()
    assert ev == {"reason_code": "contradiction_confirmed", "method": "human"}
    cstatus = tx.execute("SELECT status FROM public.contradiction_candidates WHERE id=%s",
                         [pair["pair_id"]]).fetchone()["status"]
    assert cstatus == "confirmed"
