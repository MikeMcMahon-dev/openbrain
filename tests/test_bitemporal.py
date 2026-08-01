"""ADR-018 P5 — bitemporality: valid-time decoupled from system-time.

Rolled-back transaction as the ephemeral boundary (see test_supersession_lifecycle for the
rationale); fixtures isolated on system='citest'. Skips when no DB, and skips the whole module
until migration 010 is applied (the fact_valid_until column exists) — same convention as the
lifecycle suite assuming 008/009.
"""
from __future__ import annotations

import os

import pytest

from api.temporal_query import as_of


def _has_db() -> bool:
    return bool(os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL"))


pytestmark = pytest.mark.skipif(not _has_db(), reason="no SUPABASE_DB_URL configured")


@pytest.fixture()
def tx():
    import psycopg
    from psycopg.rows import dict_row

    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn, row_factory=dict_row)
    conn.autocommit = False
    ready = conn.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name='supersession_events' "
        "AND column_name='fact_valid_until'").fetchone()
    if not ready:
        conn.close()
        pytest.skip("migration 010 (bitemporal) not applied")
    conn.execute("INSERT INTO public.system_vocabulary (system) VALUES ('citest') "
                 "ON CONFLICT DO NOTHING")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _mk(conn, ck=None, valid_from=None):
    return str(conn.execute(
        "INSERT INTO public.knowledge (content, domain, environment, system, tags, status, "
        "component_key, valid_from, source) VALUES ('citest','OpenBrain','Study','citest','{}',"
        "'current',%s, COALESCE(%s, now()), 'ci-test') RETURNING id", [ck, valid_from]
    ).fetchone()["id"])


def _evt(conn, sup, sing, occurred_at, fact_until):
    conn.execute(
        "INSERT INTO public.supersession_events (superseded_id, superseding_id, occurred_at, "
        "fact_valid_until, reason_code, method) VALUES (%s,%s,%s,%s,'manual','human')",
        [sup, sing, occurred_at, fact_until])


def test_backdated_retire_lands_fact_offset(tx):
    w = _mk(tx, "citest-w1")
    r = _mk(tx, "citest-1", valid_from="2026-06-01T00:00:00Z")
    _evt(tx, r, w, "2026-08-01T12:00:00Z", "2026-07-15T00:00:00Z")
    row = tx.execute("SELECT status, valid_until FROM public.knowledge WHERE id=%s", [r]).fetchone()
    assert row["status"] == "superseded"
    assert row["valid_until"].date().isoformat() == "2026-07-15"   # fact-offset, not occurred_at


def test_null_fact_offset_falls_back_to_occurred_at(tx):
    w = _mk(tx, "citest-w2")
    r = _mk(tx, "citest-2", valid_from="2026-08-01T08:00:00Z")
    _evt(tx, r, w, "2026-08-01T09:00:00Z", None)
    vu = tx.execute("SELECT valid_until FROM public.knowledge WHERE id=%s",
                    [r]).fetchone()["valid_until"]
    assert vu.isoformat().startswith("2026-08-01T09:00")


def test_inverted_lifespan_rejected(tx):
    w = _mk(tx, "citest-w3")
    r = _mk(tx, "citest-3", valid_from="2026-06-01T00:00:00Z")
    with pytest.raises(Exception, match="knowledge_valid_span"):
        _evt(tx, r, w, "2026-08-01T12:00:00Z", "2026-01-01T00:00:00Z")   # offset before onset


def test_as_of_respects_valid_time_window(tx):
    # r is true 2026-06-01 .. 2026-07-15 (backdated retirement); w takes over at 2026-07-15.
    w = _mk(tx, "citest-w4", valid_from="2026-07-15T00:00:00Z")
    r = _mk(tx, "citest-4", valid_from="2026-06-01T00:00:00Z")
    _evt(tx, r, w, "2026-08-01T12:00:00Z", "2026-07-15T00:00:00Z")

    ids_july1 = {row["id"] for row in as_of("2026-07-01T00:00:00Z", system="citest", conn=tx)}
    ids_aug1 = {row["id"] for row in as_of("2026-08-01T00:00:00Z", system="citest", conn=tx)}
    # valid on 2026-07-01 (inside its span) even though it is superseded NOW ...
    assert r in ids_july1
    # ... and NOT valid on 2026-08-01 (past its fact-offset); the successor is.
    assert r not in ids_aug1
    assert w in ids_aug1
