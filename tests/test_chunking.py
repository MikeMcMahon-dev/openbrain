"""Tests for heading-based chunking (ADR-017). Pure functions — no DB, no embeddings.

Run: make test  (or  cd tests && python -m pytest test_chunking.py -q)
"""
from __future__ import annotations

from api.chunking import chunk_document, collapse_chunks, infer_title, split_sections

_DOC = """# SpectreNet DNS — CURRENT STATE

This document is the authoritative current-state record for SpectreNet DNS and it
carries enough introductory detail across several sentences that it stands as its own
opening chunk without needing to borrow words from the sections that follow it below.

## Failure Domains
There are two independent failure domains in this design: the Talos VIP pool on one
side and the StanzaLab docker virtual machine on the other side of the topology. Each
one is able to serve authoritatively for the zone even if the other domain is entirely
down, which is the property that makes the whole arrangement genuinely resilient here.

## Keepalived VIP
The dot fifty-three virtual IP address is managed by keepalived in a high availability
pair spread across node A acting as master and node B acting as the backup member. The
failover path between the two members has been proven live more than once, so we treat
this section as an independent chunk that carries well beyond the minimum word count.

## Tiny
short
"""


def test_infer_title_uses_first_heading():
    assert infer_title(_DOC) == "SpectreNet DNS — CURRENT STATE"
    assert infer_title("no headings here\nsecond line") == "no headings here"
    assert infer_title("") is None


def test_split_sections_captures_headings():
    heads = [h for h, _ in split_sections(_DOC)]
    assert "SpectreNet DNS — CURRENT STATE" in heads
    assert "Failure Domains" in heads
    assert "Keepalived VIP" in heads


def test_split_sections_preamble_before_first_heading_is_none():
    doc = "leading preamble paragraph with several words before any heading\n\n## First\nbody text"
    secs = split_sections(doc)
    assert secs[0][0] is None  # preamble carries no heading
    assert secs[1][0] == "First"


def test_chunk_document_multi_section():
    chunks = chunk_document(_DOC)
    # sequential indices from 0
    assert [c["chunk_index"] for c in chunks] == list(range(len(chunks)))
    # every chunk carries the parent title in its embed text (for entity context)
    for c in chunks:
        assert c["content"].strip()
        assert "SpectreNet DNS — CURRENT STATE" in c["embed_text"]
    headings = [c["heading"] for c in chunks]
    assert "Failure Domains" in headings
    assert "Keepalived VIP" in headings
    # a mid-doc section gets the title PREFIXED (it doesn't lead with it naturally)
    fd = next(c for c in chunks if c["heading"] == "Failure Domains")
    assert fd["embed_text"].startswith("SpectreNet DNS — CURRENT STATE\n\n## Failure Domains")


def test_tiny_section_merged_not_emitted_alone():
    chunks = chunk_document(_DOC)
    # the 1-word "Tiny" section must not survive as its own fragment
    assert all(len(c["content"].split()) >= 5 for c in chunks)
    assert not any(c["heading"] == "Tiny" and len(c["content"].split()) < 40 for c in chunks)


def test_no_headings_is_single_whole_doc_chunk():
    body = "just a plain note with no markdown headings at all, a handful of words here."
    chunks = chunk_document(body)
    assert len(chunks) == 1
    assert chunks[0]["chunk_index"] == 0
    assert chunks[0]["content"] == body


def test_embed_text_not_double_prefixed_when_section_leads_with_title():
    # a section whose text already starts with the title should not get it twice
    chunks = chunk_document(_DOC)
    first = chunks[0]["embed_text"]
    assert first.count("SpectreNet DNS — CURRENT STATE") == 1


def test_empty_content_yields_no_chunks():
    assert chunk_document("") == []
    assert chunk_document("   \n  ") == []


def test_collapse_chunks_one_row_per_document():
    results = [
        {"id": "a1", "document_id": "A", "chunk_index": 0, "heading": "Intro", "score": 0.03},
        {"id": "a2", "document_id": "A", "chunk_index": 3, "heading": "Recovery", "score": 0.06},
        {"id": "b1", "document_id": "B", "chunk_index": 0, "heading": "Other", "score": 0.04},
    ]
    out = collapse_chunks(results)
    assert len(out) == 2                       # A collapsed to one row, B one row
    top = out[0]
    assert top["document_id"] == "A" and top["score"] == 0.06   # best chunk of A wins
    assert top["heading"] == "Recovery"
    sib_ids = [s["id"] for s in top["sibling_chunks"]]
    assert "a1" in sib_ids and "a2" not in sib_ids  # the non-best sibling is pointed to
    assert out[1]["document_id"] == "B"        # ordered by best score desc


def test_collapse_chunks_noop_without_document_id():
    results = [
        {"id": "x", "score": 0.03}, {"id": "y", "score": 0.02},
    ]
    out = collapse_chunks(results)
    assert [r["id"] for r in out] == ["x", "y"]
    assert all("sibling_chunks" not in r for r in out)


def test_knowledge_table_flag_resolution(monkeypatch):
    from api import knowledge_retrieval as kr
    monkeypatch.delenv("OPENBRAIN_KNOWLEDGE_TABLE", raising=False)
    assert kr._knowledge_table() == "knowledge"          # default: prod path unchanged
    monkeypatch.setenv("OPENBRAIN_KNOWLEDGE_TABLE", "knowledge_chunked")
    assert kr._knowledge_table() == "knowledge_chunked"
    monkeypatch.setenv("OPENBRAIN_KNOWLEDGE_TABLE", "chunked")
    assert kr._knowledge_table() == "knowledge_chunked"   # alias
    # chunked SELECT carries the chunk-identity columns; base does not
    assert "document_id" in kr._select_cols("knowledge_chunked")
    assert "document_id" not in kr._select_cols("knowledge")


def test_oversized_section_is_subsplit():
    big = "# T\n\n" + "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(20))
    chunks = chunk_document(big, max_words=200)
    assert len(chunks) > 1
    assert all(len(c["content"].split()) <= 260 for c in chunks)  # ~max + one paragraph
