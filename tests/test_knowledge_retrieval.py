"""Tests for api.knowledge_retrieval (OB2 cutover — Workstream A).

These run a LIVE, READ-ONLY smoke against public.knowledge to prove the
anti-collapse property: RRF fusion keyed on knowledge.id returns multiple
DISTINCT results for a broad query — unlike the legacy thoughts (file, source)
fusion bug which collapsed all text rows into one.

All 699 knowledge rows are currently status='historical', so the live smoke
overrides the default status filter with {'status': 'historical'}.

Requires .env.local with SUPABASE_DB_URL + an embedding key. Tests that need DB
or embeddings are skipped (not failed) when those are unavailable, so the file
is safe to run anywhere; the meaningful proof runs in the dev/CI env that has
credentials. Run from repo root: .venv/bin/python -m pytest tests/test_knowledge_retrieval.py -v
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Load .env.local into os.environ BEFORE importing api._openbrain_api, which
# binds DB_URL / embedding config at import time. Same pattern as
# ingest/diag_sim_rekey.py. setdefault: never clobber an already-set env var.
_ENV = ROOT / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

from api.knowledge_retrieval import (  # noqa: E402
    _build_filter_clause,
    retrieve_knowledge,
)


def _has_db() -> bool:
    return bool(
        os.getenv("SUPABASE_DB_URL")
        or os.getenv("OPENBRAIN_SUPABASE_CONNECTION_STRING")
        or os.getenv("DATABASE_URL")
    )


def _has_embeddings() -> bool:
    return bool(os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY"))


needs_db = pytest.mark.skipif(not _has_db(), reason="no SUPABASE_DB_URL in env")


# --------------------------------------------------------------------------- #
# Pure unit tests — no DB, no network                                         #
# --------------------------------------------------------------------------- #

def test_filter_clause_defaults_to_current():
    clause, params = _build_filter_clause(owner=None, filters=None)
    assert "status = %s" in clause
    assert params == ["current"]


def test_filter_clause_status_override():
    clause, params = _build_filter_clause(owner=None, filters={"status": "historical"})
    assert params == ["historical"]


def test_filter_clause_status_disabled_with_none():
    clause, params = _build_filter_clause(owner=None, filters={"status": None})
    assert "status" not in clause
    assert params == []


def test_filter_clause_owner_and_facets():
    clause, params = _build_filter_clause(
        owner="mike.mcmahon67",
        filters={"status": "historical", "domain": "Network", "environment": "Lab"},
    )
    assert "created_by = %s" in clause
    assert "domain = %s" in clause
    assert "environment = %s" in clause
    # status, owner, domain, environment — order is deterministic
    assert params == ["historical", "mike.mcmahon67", "Network", "Lab"]


def test_filter_clause_ignores_unknown_keys():
    clause, params = _build_filter_clause(
        owner=None, filters={"status": "current", "bogus": "x"}
    )
    assert "bogus" not in clause
    assert params == ["current"]


# --------------------------------------------------------------------------- #
# Live, READ-ONLY smoke tests against public.knowledge                         #
# --------------------------------------------------------------------------- #

@needs_db
def test_current_status_filter_applied():
    """The default status='current' filter is actually applied: every returned row is
    'current'. (Pre-flip this asserted emptiness against 0 'current' rows; the OB2
    cutover+promote created current rows, so we assert the filter holds rather than a
    transient row count.)"""
    results = retrieve_knowledge("network configuration", n_results=10, owner=None)
    assert all(r.get("status") == "current" for r in results)


@needs_db
def test_anti_collapse_multiple_distinct_results():
    """THE ANTI-COLLAPSE PROOF.

    A broad query over historical rows must return >1 DISTINCT knowledge.id.
    Under the legacy (file, source) fusion key this would collapse to exactly 1.
    """
    if not _has_embeddings():
        pytest.skip("no embedding key; keyword-only path still proves anti-collapse")

    results = retrieve_knowledge(
        "configuration", n_results=10, owner=None, filters={"status": "historical"}
    )
    ids = [r["id"] for r in results]
    distinct = set(ids)
    print(f"\n[anti-collapse] returned {len(results)} results, "
          f"{len(distinct)} distinct ids")
    for r in results[:5]:
        print(f"  id={r['id'][:8]} score={r['score']:.5f} conf={r['confidence']} "
              f"domain={r['domain']} text={r['text'][:50]!r}")
    assert len(results) > 1, "fusion collapsed to <=1 result (the legacy bug)"
    assert len(distinct) == len(ids), "duplicate ids in output — fusion key not unique"
    assert len(distinct) > 1, "expected >1 distinct knowledge.id"


@needs_db
def test_keyword_only_anti_collapse():
    """Even without embeddings, the keyword candidate path alone must yield
    multiple distinct ids for a broad term — isolates the fusion key from the
    embedding service."""
    from api.knowledge_retrieval import search_knowledge_keyword_candidates

    cands = search_knowledge_keyword_candidates(
        "configuration", owner=None, max_results=20, filters={"status": "historical"}
    )
    ids = {c["id"] for c in cands}
    print(f"\n[keyword-only] {len(cands)} candidates, {len(ids)} distinct ids")
    assert len(ids) > 1


@needs_db
def test_result_contract_shape():
    """Every returned dict carries the exact Phase-2 contract keys."""
    results = retrieve_knowledge(
        "system", n_results=5, owner=None, filters={"status": "historical"}
    )
    if not results:
        pytest.skip("no historical rows matched 'system'")
    # `signals` added by ADR-014 (raw per-retriever instrument behind the fused score).
    expected = {"id", "text", "score", "confidence",
                "domain", "environment", "system", "tags", "status", "signals"}
    for r in results:
        assert set(r.keys()) == expected
        assert isinstance(r["id"], str)
        assert isinstance(r["tags"], list)
        assert r["status"] == "historical"


@needs_db
def test_n_results_respected():
    results = retrieve_knowledge(
        "configuration", n_results=3, owner=None, filters={"status": "historical"}
    )
    assert len(results) <= 3


@needs_db
def test_owner_scope():
    """owner scopes via created_by; mike.mcmahon67 owns 645 historical rows."""
    results = retrieve_knowledge(
        "configuration",
        n_results=10,
        owner="mike.mcmahon67",
        filters={"status": "historical"},
    )
    # all results (if any) must be reachable; we can't read created_by from the
    # contract shape, so just assert the call succeeds and is well-formed.
    assert isinstance(results, list)


# --- Length penalty is skipped on chunked reads (Chat's eval, 2026-07-30) -------
# Fully mocked (no DB): patch the two candidate searches + embedding so the fusion,
# length-penalty, and collapse logic run deterministically on a synthetic short row.
def _short_row(chunked: bool):
    row = {"id": "11111111-1111-1111-1111-111111111111",
           "content": " ".join(["word"] * 27),  # 27 words — under the 30-word threshold
           "domain": "OpenBrain", "environment": "Production", "system": None,
           "tags": [], "status": "current", "score": 0.2}
    if chunked:
        row.update(document_id="22222222-2222-2222-2222-222222222222",
                   chunk_index=0, heading="Mounting")
    return row


def _run_retrieve(row, table):
    from api import knowledge_retrieval as kr
    with patch.object(kr, "embedding_request", return_value=[0.1] * 8), \
         patch.object(kr, "search_knowledge_vector_candidates", return_value=[dict(row)]), \
         patch.object(kr, "search_knowledge_keyword_candidates", return_value=[dict(row)]):
        return kr.retrieve_knowledge("q", 5, owner=None, filters={"status": None}, table=table)


def test_length_penalty_skipped_on_chunked_reads():
    res = _run_retrieve(_short_row(chunked=True), "knowledge_chunked")
    assert res and res[0]["signals"]["word_count"] == 27
    # a 27-word section is intentional under chunking — must NOT be docked
    assert res[0]["signals"]["length_penalty_applied"] == 1.0


def test_length_penalty_still_applies_on_base_knowledge():
    res = _run_retrieve(_short_row(chunked=False), "knowledge")
    assert res[0]["signals"]["word_count"] == 27
    # whole-doc reads keep the ADR-002 noise penalty
    assert abs(res[0]["signals"]["length_penalty_applied"] - 27 / 30) < 1e-9


# --- Recency net (ADR-018 P1): old current docs sink in allowlisted domains -----
from datetime import datetime, timedelta, timezone  # noqa: E402


def _dated(cid, days_old, *, domain="Network", tags=None, component=False):
    t = list(tags or [])
    if component:
        t.append("component:x")
    return {"id": cid, "content": "network configuration note, enough words to matter",
            "domain": domain, "environment": "Production", "system": None, "tags": t,
            "status": "current",
            "created_at": datetime.now(timezone.utc) - timedelta(days=days_old)}


def _rank(rows):
    from api import knowledge_retrieval as kr
    cands = [dict(r) for r in rows]
    with patch.object(kr, "embedding_request", return_value=[0.1] * 8), \
         patch.object(kr, "search_knowledge_vector_candidates", return_value=cands), \
         patch.object(kr, "search_knowledge_keyword_candidates", return_value=cands):
        res = kr.retrieve_knowledge("q", 5, owner=None, filters={"status": None}, table="knowledge")
    return [r["id"] for r in res]


def test_recency_off_by_default_preserves_order(monkeypatch):
    monkeypatch.setattr("api.knowledge_retrieval._RECENCY_HALFLIFE_DAYS", 0.0)
    # old ranks first on both retrievers; with recency OFF it stays first
    assert _rank([_dated("old", 300), _dated("new", 3)])[0] == "old"


def test_recency_sinks_old_below_new_when_enabled(monkeypatch):
    monkeypatch.setattr("api.knowledge_retrieval._RECENCY_HALFLIFE_DAYS", 90.0)
    order = _rank([_dated("old", 300), _dated("new", 3)])
    assert order.index("new") < order.index("old")   # 300-day note sinks below the 3-day one


def test_recency_exempts_durable(monkeypatch):
    monkeypatch.setattr("api.knowledge_retrieval._RECENCY_HALFLIFE_DAYS", 90.0)
    # a durable-tagged old doc must NOT sink — stays above the fresher note
    assert _rank([_dated("old", 300, tags=["durable"]), _dated("new", 3)])[0] == "old"


def test_recency_exempts_component_keyed(monkeypatch):
    monkeypatch.setattr("api.knowledge_retrieval._RECENCY_HALFLIFE_DAYS", 90.0)
    assert _rank([_dated("old", 300, component=True), _dated("new", 3)])[0] == "old"


def test_recency_exempts_non_allowlisted_domain(monkeypatch):
    monkeypatch.setattr("api.knowledge_retrieval._RECENCY_HALFLIFE_DAYS", 90.0)
    # Study is outside the allowlist (protects Annie's tutor) — old Study doc doesn't sink
    assert _rank([_dated("old", 300, domain="Study"), _dated("new", 3, domain="Study")])[0] == "old"
