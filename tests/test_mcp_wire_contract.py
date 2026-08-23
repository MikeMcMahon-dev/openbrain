"""Wire-contract tests — exercise `mcp_http.handler()` end to end, not the Python functions.

WHY THIS FILE EXISTS

`plan_ingest` shipped in #106 and raised "Object of type Decimal is not JSON serializable" on
every call in production. Every test covering it passed, because they all called `build_plan()`
directly or stubbed it. The object was correct in Python; it only failed being written out.

Postgres `round(extract(epoch ...))` returns numeric, which psycopg maps to Decimal, and Decimal
has no JSON encoder. That type exists only at the serialization boundary — no amount of testing
BELOW that boundary can see it.

`handler()` is the boundary. It is the same entry point Vercel invokes, it runs the real routing,
auth, tool dispatch and `json.dumps`, and it needs no deployment and no HTTP server. So a wire
test here costs milliseconds and catches an entire class of bug that unit tests structurally
cannot: anything that is fine as an object and broken as a payload.

Verified to actually catch the original defect: run against the pre-fix commit, the plan_ingest
case below returns the exact production error string.

These need the live vault (the plan reads real living docs), so they skip without SUPABASE_DB_URL.

Run: cd tests && ../.venv/bin/python -m pytest test_mcp_wire_contract.py -q
"""
from __future__ import annotations

import json
import os

import pytest

from api.mcp_http import handler

pytestmark = pytest.mark.skipif(
    not (os.getenv("SUPABASE_DB_URL") and os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN")),
    reason="wire tests need the live vault and a token",
)


def _call(name: str, arguments: dict) -> tuple[int, dict]:
    """Invoke a tool exactly as Vercel would: JSON in, JSON out."""
    request = {
        "method": "POST",
        "headers": {
            "authorization": f"Bearer {os.environ['OPENBRAIN_TOOL_ACCESS_TOKEN']}",
            "content-type": "application/json",
        },
        "body": json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }),
    }
    response = handler(request)
    return response["statusCode"], json.loads(response["body"])


def _unwrap(body: dict) -> dict:
    """Pull the tool payload out of the JSON-RPC envelope, failing loudly on a tool error.

    A tool failure comes back as HTTP 200 with an `error` member inside the envelope — which is
    why a status-code check alone would have called the Decimal bug a success.
    """
    assert "error" not in body, f"tool returned an error: {body['error']}"
    return json.loads(body["result"]["content"][0]["text"])


def test_plan_ingest_serializes_over_the_wire():
    # The exact minimal payload from the production bug report.
    status, body = _call("plan_ingest", {
        "source": "Test.", "system": "FlightSim", "component": "flightsim-hardware"})
    assert status == 200
    assert "error" not in body, (
        f"plan_ingest failed over the wire: {body.get('error', {}).get('message')}")


def test_plan_ingest_returns_a_usable_plan():
    status, body = _call("plan_ingest", {"source": "Test.", "system": "FlightSim"})
    plan = _unwrap(body)
    assert plan["plan_token"], "no token issued — apply would be impossible"
    assert isinstance(plan["candidates"], list)
    for doc in plan["current_state"]["living_docs_in_system"]:
        assert isinstance(doc["age_days"], int)
    for doc in plan["current_state"]["similar_living_docs"]:
        assert isinstance(doc["similarity"], float)


def test_plan_ingest_reports_what_it_would_supersede():
    status, body = _call("plan_ingest", {
        "source": "Test.", "system": "FlightSim", "component": "flightsim-hardware"})
    plan = _unwrap(body)
    assert plan["would_supersede"] is not None, "declared an existing component but no target"
    assert plan["would_supersede"]["component_key"] == "flightsim-hardware"


def test_every_read_tool_survives_serialization():
    """Blanket guard: any read tool whose response cannot be written out fails here.

    Cheap insurance against the same class reappearing in a tool nobody thought to wire-test.
    """
    for name, args in (
        ("query", {"query": "flight sim rig", "n_results": 2}),
        ("search", {"query": "flight sim rig", "n_results": 2}),
        ("plan_ingest", {"source": "Test.", "system": "FlightSim"}),
    ):
        status, body = _call(name, args)
        assert status == 200, f"{name} -> {status}"
        assert "error" not in body, f"{name} failed: {body.get('error', {}).get('message')}"


def test_tools_list_is_serializable_and_complete():
    request = {
        "method": "POST",
        "headers": {"authorization": f"Bearer {os.environ['OPENBRAIN_TOOL_ACCESS_TOKEN']}"},
        "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    }
    body = json.loads(handler(request)["body"])
    names = {t["name"] for t in body["result"]["tools"]}
    assert {"query", "search", "fetch", "ingest", "plan_ingest"} <= names, names
