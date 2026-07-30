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
