"""Supersession test harness — Suite C (retrieval), the C3/C4 regression pair.

Companion to chat_handoff/openbrain-supersession-plan.md + openbrain-test-harness.md
and docs/decisions/ADR-018. Built FIRST, against the system as it stands today, with
red-green discipline: C3 (boost-off) *should fail now* — it reproduces the measured
stale-ranking bug — and C4 (evergreen negative control) *should pass*. Watching them do
exactly that is what proves the harness detects the defect.

Design choices (settled with Chat, 2026-07-31):
- **No DB, no live embeddings.** Suite C is pure ranking logic, so fixtures are frozen
  candidate rows fed through the real fusion/boost/collapse path via the candidate-search
  seam. Deterministic, CI-safe, and — per Chat's Q5 — zero risk of fixture contradictions
  polluting the production store. Suites A/B/E need a real ephemeral schema for triggers
  and DDL; they land with their phases.
- **Every temporal assertion runs at boost=2.0 AND boost=1.0.** The measured failure was
  invisible with the boost on (it fires on 3 of 731 rows). Boost-off is the honest test.
- **Full response path.** Assertions run through retrieve_for_query (fusion → adapt →
  contract), not an internal ranker — the seam the sibling_chunks bug hid in.

Run: make test-supersession-regression   (C3 + C4 only)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from api import _openbrain_api as ob
from api import knowledge_retrieval as kr

OWNER = "mike.mcmahon67"

# --- Fixture corpus (frozen). Candidate-row shape = what the search_* functions return.
# `created_at` is carried even though today's SELECT ignores it — P1 (recency) will read it,
# and these dates are what will flip C3 from red to green. -----------------------------------

def _chunk(cid, doc, content, *, system, tags, created_at, heading):
    return {
        "id": cid, "document_id": doc, "chunk_index": 0, "heading": heading,
        "content": content, "domain": "Network", "environment": "Production",
        "system": system, "tags": tags, "status": "current", "created_by": OWNER,
        "created_at": created_at,
        # vector candidate score = cosine DISTANCE (0=identical); similarity = 1 - dist.
        "score": None,  # set per-list below; ordering in the list is the RRF rank.
    }

# F3a — June event note asserting the (now-decommissioned) infra is healthy. NOT a living doc.
F3A = _chunk(
    "aaaaaaaa-0000-0000-0000-000000000001", "doc-june-3a",
    "Technitium nodes now run on the Talos Kubernetes cluster. Status: COMPLETE and healthy.",
    system="SpectreNet", tags=["DNS"], created_at="2026-06-14", heading=None,
)
# F3b — July authoritative current-state doc that supersedes the June claim. Component-tagged.
F3B = _chunk(
    "bbbbbbbb-0000-0000-0000-000000000002", "doc-july-3b",
    "SpectreNet DNS CURRENT STATE 2026-07-19: Technitium is DECOMMISSIONED. CoreDNS authoritative.",
    system="SpectreNet", tags=["DNS", "component:dns-current-state"],
    created_at="2026-07-19", heading="CURRENT STATE as of 2026-07-19",
)
# F4 — evergreen architecture doc: old, no newer competitor, would be a durable-marked row.
# Negative control: must not be demoted when the recency net lands (durable exemption).
F4 = _chunk(
    "cccccccc-0000-0000-0000-000000000003", "doc-evergreen-4",
    "SpectreNet VLAN topology: VLAN10 trusted, VLAN30 IoT. Core switch trunks. Stable design.",
    system="SpectreNet", tags=["Network", "durable"], created_at="2026-03-01",
    heading="VLAN Topology",
)


def _run(candidates, boost, monkeypatch):
    """Feed frozen candidates through the real retrieval path at a given component boost.
    Both retrievers see the same ordered list, so list order IS the RRF rank."""
    monkeypatch.setenv("OPENBRAIN_READ_TARGET", "knowledge")
    monkeypatch.setenv("OPENBRAIN_KNOWLEDGE_TABLE", "knowledge_chunked")
    monkeypatch.setattr(kr, "_COMPONENT_BOOST", boost)
    vec = [dict(c, score=0.36 + i * 0.01) for i, c in enumerate(candidates)]   # distances
    key = [dict(c, score=0.05 - i * 0.005) for i, c in enumerate(candidates)]  # ts_ranks
    with patch.object(kr, "embedding_request", return_value=[0.1] * 8), \
         patch.object(kr, "search_knowledge_vector_candidates", return_value=vec), \
         patch.object(kr, "search_knowledge_keyword_candidates", return_value=key):
        return ob.retrieve_for_query("technitium dns current state", 5, OWNER, "family")


def _order(results):
    return [r.get("document_id") for r in results]


# --- C3: the regression pair. F3a (June-stale) vs F3b (July-authoritative). ------------------

def test_C3_boost_on_july_authoritative_wins(monkeypatch):
    """boost=2.0 — the workaround. The component boost lifts the authoritative July doc
    above the stale June note. This SHOULD pass today; it measures that the workaround
    still works, not that the underlying ranking is correct."""
    order = _order(_run([F3A, F3B], boost=2.0, monkeypatch=monkeypatch))
    assert order.index("doc-july-3b") < order.index("doc-june-3a")


@pytest.mark.xfail(
    strict=True,
    reason="P1 recency net not implemented. Boost-off, the June-stale note (older) still "
    "outranks the July-authoritative doc — the measured bug. Flip to a hard pass when P1 "
    "lands (created_at recency sinks the older note). strict=True makes the fix announce itself.",
)
def test_C3_boost_off_july_authoritative_should_win(monkeypatch):
    """boost=1.0 — the honest test. With the boost off (its state for 99.6% of the corpus),
    recency-blind fusion lets the 5-week-older June note outrank the July authoritative doc.
    Must hold once P1 ships."""
    order = _order(_run([F3A, F3B], boost=1.0, monkeypatch=monkeypatch))
    assert order.index("doc-july-3b") < order.index("doc-june-3a")


def test_C3_bug_is_reproduced_at_boost_off(monkeypatch):
    """Green proof of the red state: assert the CURRENT (buggy) order so the harness
    demonstrably detects the defect. When P1 flips C3 above, this reproduction test flips
    too and gets removed alongside it."""
    order = _order(_run([F3A, F3B], boost=1.0, monkeypatch=monkeypatch))
    assert order.index("doc-june-3a") < order.index("doc-july-3b")  # June wrongly wins, today


# --- C4: negative control. Evergreen must not be silently demoted. ---------------------------

def test_C4_evergreen_not_demoted_boost_on(monkeypatch):
    """F4 (old, evergreen) is the top match and stays the top result. Over-retirement/
    over-demotion fails silently, so this pairs with every 'stale must sink' assertion.
    Must remain green through every phase — including after the recency net (durable exempt)."""
    order = _order(_run([F4], boost=2.0, monkeypatch=monkeypatch))
    assert order[0] == "doc-evergreen-4"


def test_C4_evergreen_not_demoted_boost_off(monkeypatch):
    order = _order(_run([F4], boost=1.0, monkeypatch=monkeypatch))
    assert order[0] == "doc-evergreen-4"
