"""Regression test for the MCP ingest tool taxonomy-forwarding bug (fixed 2026-07-19).

The `ingest` tool's inputSchema advertises `domain`/`environment`/`tags`, but
`_call_tool` built the normalized payload from only source_type/source/subject/topic
— silently dropping the taxonomy override before `ingest_payload` could honor it.
Effect: technical content sent with domain=Network still landed in the Study default.

The pure mapper (test_taxonomy_map.py) was always green; nothing tested this glue.
These tests assert the fields are forwarded when present, and NOT fabricated when
absent (so the Family-GPT paths, which never send them, still fall back to inference).

Run: .venv/bin/python -m pytest tests/test_mcp_ingest_forwarding.py -q
"""

from __future__ import annotations

from unittest.mock import patch

from api import mcp_http


def _capture_ingest(args):
    captured = {}

    def fake_ingest_payload(normalized, metadata):
        captured["normalized"] = normalized
        return 200, {"status": "accepted", "ingest_id": "test"}

    with patch.object(mcp_http, "ingest_payload", side_effect=fake_ingest_payload):
        mcp_http._call_tool("ingest", args, metadata={})
    return captured["normalized"]


def test_ingest_tool_forwards_taxonomy_override():
    n = _capture_ingest(
        {
            "source_type": "text",
            "source": "probe body",
            "subject": "throwaway",
            "topic": "throwaway",
            "domain": "Network",
            "environment": "Production",
            "tags": ["SpectreNet"],
        }
    )
    assert n.get("domain") == "Network", "domain override dropped by the ingest tool"
    assert n.get("environment") == "Production", "environment override dropped by the ingest tool"
    assert n.get("tags") == ["SpectreNet"], "tags dropped by the ingest tool"


def test_ingest_tool_does_not_fabricate_taxonomy_when_absent():
    # Family/GPT ingests never send domain/environment; the tool must not invent them,
    # so ingest_payload falls back to map_to_taxonomy inference. Guards the family UX.
    n = _capture_ingest(
        {"source_type": "text", "source": "annie note", "subject": "Science", "topic": "prep"}
    )
    assert "domain" not in n, "must not fabricate a domain when the client omits it"
    assert "environment" not in n, "must not fabricate an environment when the client omits it"
