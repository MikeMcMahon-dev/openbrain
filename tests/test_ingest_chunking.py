"""Ingest-time chunking dual-write (ADR-017 Phase H). Mocked — no DB, no embeddings.
The INSERT SQL itself is exercised for real by the 1,241-row backfill; here we assert
the flow: gated off by default, one INSERT per chunk with the parent document_id, and
— post ADR-018a item 3 — a CONTENT-ONLY mirror: no metadata columns, and the tag-based
chunk-status UPDATE is gone (metadata joins from the parent, so it cannot drift).

Run: make test  (or  cd tests && python -m pytest test_ingest_chunking.py -q)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from api import knowledge_ingest as ki

_DOC = """# SpectreNet DNS — CURRENT STATE

This opening section is the authoritative current-state record for SpectreNet DNS and it
deliberately carries enough introductory detail across several sentences here that it can
stand as its own opening chunk without needing to borrow any words at all from the two
sections that follow it further down in the body of this particular document below.

## Failure Domains
There are two fully independent failure domains in this design: the Talos VIP pool on the
one side and the StanzaLab docker virtual machine on the other side of the topology, and
each one of them is able to serve authoritatively for the whole zone even when the other
domain is entirely down, which is exactly the property that makes the arrangement resilient.

## Keepalived VIP
The dot fifty-three virtual IP address is managed by keepalived in a high availability pair
that is spread across node A acting as the master member and node B acting as the backup,
and the failover path between the two members has now been proven live more than one time,
so this section carries comfortably beyond the minimum word count to be an independent chunk.
"""


def _fake_conn():
    calls = []
    conn = MagicMock()
    conn.execute = MagicMock(side_effect=lambda sql, params=None: calls.append((sql, params)))
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    return cm, calls


def test_chunk_on_ingest_flag(monkeypatch):
    monkeypatch.delenv("OPENBRAIN_CHUNK_ON_INGEST", raising=False)
    assert ki._chunk_on_ingest() is False           # default: held in reserve
    for on in ("1", "true", "on", "yes"):
        monkeypatch.setenv("OPENBRAIN_CHUNK_ON_INGEST", on)
        assert ki._chunk_on_ingest() is True


def test_dual_write_inserts_one_row_per_chunk_with_parent_doc_id():
    cm, calls = _fake_conn()
    with patch.object(ki, "get_db_conn", return_value=cm), \
         patch.object(ki, "embedding_request", return_value=[0.1] * 1536):
        ki._dual_write_chunks(
            "doc-uuid-1", _DOC, "mike.mcmahon67",
            source="ingest", supersedes_id=None, ingest_id="ing1")
    inserts = [c for c in calls if "INSERT INTO public.knowledge_chunked" in c[0]]
    assert len(inserts) >= 2                          # multi-section doc -> N chunks
    assert all(params[2] == "doc-uuid-1" for _, params in inserts)   # parent document_id
    assert [params[3] for _, params in inserts] == list(range(len(inserts)))  # chunk_index
    # Content-only mirror (ADR-018a item 3): the INSERT names no metadata columns.
    insert_sql = inserts[0][0]
    for col in ("system", "tags", "status", "domain", "environment", "component_key"):
        assert col not in insert_sql


def test_dual_write_never_issues_status_update():
    # ADR-018a P2 step 3: chunks carry no status; the tag-based chunk-status UPDATE (the
    # second reconciliation failure mode) is removed. The mirror is append-only, always.
    cm, calls = _fake_conn()
    with patch.object(ki, "get_db_conn", return_value=cm), \
         patch.object(ki, "embedding_request", return_value=[0.1] * 1536):
        ki._dual_write_chunks(
            "doc-uuid-2", _DOC, "mike.mcmahon67",
            source="ingest", supersedes_id=None, ingest_id="ing2")
    assert not [c for c in calls if "UPDATE public.knowledge_chunked" in c[0]]


def test_dual_write_empty_content_is_noop():
    cm, calls = _fake_conn()
    with patch.object(ki, "get_db_conn", return_value=cm), \
         patch.object(ki, "embedding_request", return_value=[0.1] * 1536):
        ki._dual_write_chunks(
            "doc-uuid-3", "   ", "mike.mcmahon67",
            source="ingest", supersedes_id=None, ingest_id="ing3")
    assert calls == []
