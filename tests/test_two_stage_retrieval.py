"""Tests for two-stage retrieval (ADR-016): skim previews + fetch-by-id.

Covers the response de-duplication (Phase 0), the skim/fetch payload shapes and
their MCP glue (Phase 1/2), and — most importantly — the fetch owner-scoping
guard: the owner used to scope a fetch comes from the authenticated request
context, never from the client-supplied body, so a caller cannot fetch another
tenant's note by id.

Run: .venv/bin/python -m pytest tests/test_two_stage_retrieval.py -q
"""
from __future__ import annotations

from unittest.mock import patch

from api import _openbrain_api as ob
from api import mcp_http


# --- pure projections -------------------------------------------------------
def test_snippet_trims_long_text():
    text = " ".join(str(i) for i in range(100))
    snip = ob._snippet(text, words=40)
    assert snip.endswith("…")
    assert len(snip.split()) == 41  # 40 words + the ellipsis token


def test_snippet_passthrough_short_text():
    assert ob._snippet("just a few words") == "just a few words"


def test_slim_result_replaces_text_with_snippet_keeps_keys():
    full = {"id": "x", "text": " ".join(["w"] * 200), "status": "current",
            "domain": "Network", "signals": {"vector_similarity": 0.6}}
    slim = ob._slim_result(full)
    assert len(slim["text"].split()) < 200          # trimmed
    assert slim["status"] == "current"              # metadata preserved
    assert slim["signals"] == {"vector_similarity": 0.6}


def test_skim_result_drops_full_text():
    full = {"id": "x", "document_id": "x", "text": " ".join(["w"] * 200),
            "section": "SpectreNet DNS", "system": "SpectreNet", "domain": "Network",
            "status": "current", "tags": ["component:dns-current-state"],
            "score": 0.03, "confidence": "high", "signals": {"retrievers_hit": 2}}
    skim = ob._skim_result(full)
    assert "text" not in skim                        # NO full text
    assert skim["snippet"] and len(skim["snippet"].split()) <= 41
    assert skim["word_count"] == 200
    assert skim["heading"] == "SpectreNet DNS"
    assert skim["signals"] == {"retrievers_hit": 2}


# --- fetch owner-scoping (the security property) ----------------------------
def _md(owner):
    return {"method": "POST", "headers": {"x-openbrain-owner": owner}}


def test_fetch_scopes_to_authenticated_owner_not_body():
    """The owner passed to the DB layer must come from the request context, even
    when the client tries to spoof a different owner in the payload body."""
    seen = {}

    def fake_fetch(ids, owner):
        seen["ids"], seen["owner"] = ids, owner
        return []

    with patch.object(ob, "request_context", return_value=("anneliesepaige", "family")):
        with patch("api.knowledge_retrieval.fetch_knowledge_by_ids", side_effect=fake_fetch):
            # body tries to spoof owner=mike — must be ignored
            ob.fetch_payload({"ids": ["a", "b"], "owner": "mike.mcmahon67"}, _md("anneliesepaige"))
    assert seen["owner"] == "anneliesepaige"
    assert seen["ids"] == ["a", "b"]


def test_fetch_empty_ids_is_400():
    status, body = ob.fetch_payload({"ids": []}, _md("mike.mcmahon67"))
    assert status == 400
    assert body["error"] == "validation_error"


def test_fetch_caps_id_count():
    seen = {}

    def fake_fetch(ids, owner):
        seen["n"] = len(ids)
        return []

    many = [str(i) for i in range(50)]
    with patch.object(ob, "request_context", return_value=("mike.mcmahon67", "family")):
        with patch("api.knowledge_retrieval.fetch_knowledge_by_ids", side_effect=fake_fetch):
            ob.fetch_payload({"ids": many}, _md("mike.mcmahon67"))
    assert seen["n"] == ob._MAX_FETCH_IDS


def test_fetch_by_ids_empty_owner_returns_empty_without_db():
    """A falsy owner must never widen scope — return [] and never touch the DB."""
    from api import knowledge_retrieval as kr
    with patch.object(kr, "get_db_conn") as conn:
        assert kr.fetch_knowledge_by_ids(["11111111-1111-1111-1111-111111111111"], "") == []
        conn.assert_not_called()


def test_fetch_by_ids_drops_non_uuid_ids():
    from api import knowledge_retrieval as kr
    with patch.object(kr, "get_db_conn") as conn:
        # all ids invalid -> no query, empty result
        assert kr.fetch_knowledge_by_ids(["not-a-uuid", "123"], "mike.mcmahon67") == []
        conn.assert_not_called()


# --- MCP glue (Phase 2) -----------------------------------------------------
def test_mcp_search_routes_to_search_payload():
    captured = {}

    def fake_search(normalized, metadata):
        captured["normalized"] = normalized
        return 200, {"results": [], "count": 0}

    with patch.object(mcp_http, "search_payload", side_effect=fake_search):
        mcp_http._call_tool("search", {"query": "dns", "n_results": 3}, metadata={})
    assert captured["normalized"]["query"] == "dns"
    assert captured["normalized"]["n_results"] == 3


def test_mcp_fetch_routes_to_fetch_payload_with_ids():
    captured = {}

    def fake_fetch(payload, metadata):
        captured["payload"] = payload
        return 200, {"notes": [], "count": 0}

    with patch.object(mcp_http, "fetch_payload", side_effect=fake_fetch):
        mcp_http._call_tool("fetch", {"ids": ["id1", "id2"]}, metadata={})
    assert captured["payload"]["ids"] == ["id1", "id2"]


def test_mcp_advertises_search_and_fetch_tools():
    names = {t["name"] for t in mcp_http._list_tools()}
    assert {"search", "fetch"} <= names


# --- chunked skim/fetch identity (ADR-017) ----------------------------------
def test_skim_carries_document_id_and_siblings():
    r = {"id": "chunk1", "document_id": "docA", "heading": "Recovery", "text": "w " * 100,
         "score": 0.03, "signals": {}, "sibling_chunks": [{"id": "chunk2", "heading": "Sec2"}]}
    skim = ob._skim_result(r)
    assert skim["id"] == "chunk1"           # the section to fetch
    assert skim["document_id"] == "docA"    # the whole-doc handle
    assert skim["sibling_chunks"] == [{"id": "chunk2", "heading": "Sec2"}]
    assert "text" not in skim               # still no full body in a skim


def test_shape_fetch_carries_chunk_identity_when_present():
    from api import knowledge_retrieval as kr
    chunk = {"id": "c1", "content": "body", "document_id": "docA", "chunk_index": 2,
             "heading": "Recovery", "domain": "K8s", "system": "pmx-01", "status": "current",
             "tags": [], "environment": "Lab"}
    out = kr._shape_fetch(chunk)
    assert out["document_id"] == "docA" and out["chunk_index"] == 2
    assert out["heading"] == "Recovery" and out["section"] == "Recovery"
    plain = kr._shape_fetch({"id": "k1", "content": "b", "domain": "K8s", "system": None,
                             "status": "current", "tags": [], "environment": "Lab"})
    assert "document_id" not in plain       # unchunked row stays clean
