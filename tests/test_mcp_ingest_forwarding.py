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


# --------------------------------------------------------------------------------------
# ADR-008/018 living-doc identity (added 2026-08-22).
#
# The backend has accepted `system`/`component`/`valid_from` since ADR-018 P2/P5, but no MCP
# surface advertised them, so no MCP client could key a living doc — a re-ingest appended a
# second competing `current` row instead of superseding. That stranded the FlightSim doc as
# a keyless row on 2026-08-21.
#
# There are two independent ways to reintroduce the bug, so both are guarded:
#   1. schema advertises the field, `_call_tool`'s allowlist drops it (silent keyless write
#      that returns 200 and looks successful — the nastier one)
#   2. `_call_tool` forwards it, schema never advertises it (client can't discover it)
# --------------------------------------------------------------------------------------

_IDENTITY_FIELDS = ("system", "component", "valid_from")


def _ingest_schema_properties(tool_list):
    ingest = next(t for t in tool_list if t["name"].endswith("ingest"))
    return ingest["inputSchema"]["properties"]


def test_ingest_tool_forwards_living_doc_identity():
    n = _capture_ingest(
        {
            "source_type": "text",
            "source": "flight sim rig body",
            "subject": "FlightSim",
            "topic": "flightsim-hardware",
            "domain": "Personal",
            "environment": "Lab",
            "system": "FlightSim",
            "component": "flightsim-hardware",
            "valid_from": "2026-08-21",
        }
    )
    assert n.get("system") == "FlightSim", "system dropped — living doc would land keyless"
    assert n.get("component") == "flightsim-hardware", "component dropped — no supersession pivot"
    assert n.get("valid_from") == "2026-08-21", "valid_from dropped — ADR-018 bitemporality"


def test_ingest_tool_does_not_fabricate_identity_when_absent():
    # Append-only notes (session wraps, family ingests) must stay keyless: a fabricated
    # component would make every wrap retire the previous one.
    n = _capture_ingest(
        {"source_type": "text", "source": "session wrap", "subject": "session-context"}
    )
    for field in _IDENTITY_FIELDS:
        assert field not in n, f"must not fabricate {field} when the client omits it"


def test_hosted_schema_advertises_identity_fields():
    props = _ingest_schema_properties(mcp_http._list_tools())
    for field in _IDENTITY_FIELDS:
        assert field in props, f"hosted MCP ingest schema does not advertise {field}"
    assert "FlightSim" in props["system"]["enum"], "system enum drifted from CANONICAL_SYSTEMS"


def test_stdio_schema_advertises_identity_fields():
    # The stdio server forwards `arguments` wholesale, so its schema is the only gate.
    import asyncio

    from mcp_server import openbrain as stdio

    tools = asyncio.run(stdio.list_tools())
    props = _ingest_schema_properties(
        [{"name": t.name, "inputSchema": t.inputSchema} for t in tools]
    )
    for field in _IDENTITY_FIELDS:
        assert field in props, f"stdio MCP ingest schema does not advertise {field}"


def test_hosted_schema_and_forwarding_stay_in_sync():
    # The trap: advertising a field the allowlist drops yields a 200 + a keyless row.
    props = _ingest_schema_properties(mcp_http._list_tools())
    forwarded = _capture_ingest(
        {
            "source_type": "text",
            "source": "body",
            "subject": "s",
            "topic": "t",
            "domain": "OpenBrain",
            "environment": "Lab",
            "tags": ["OpenBrain"],
            "system": "OpenBrain",
            "component": "probe",
            "valid_from": "2026-08-22",
            # Plan/apply handshake fields. If the schema advertises these and _call_tool drops
            # them, every gated commit 409s with no way for the client to comply.
            "plan_token": "probe.token",
            "acknowledged_not_updating": ["some-component"],
            "decline_reason": "probe",
        }
    )
    missing = [p for p in props if p not in forwarded]
    assert not missing, f"inputSchema advertises {missing} but _call_tool silently drops them"
