"""Every ingest surface must behave the same way.

OpenBrain exposes ingest through four HTTP surfaces plus a stdio client, and the recurring bug
is not a broken surface — it is a DIVERGENT one. The same three-part failure has now happened
three times:

  * `system`/`component` were accepted by the backend but not forwarded by MCP, so living-doc
    identity was unreachable there for weeks while every other surface had it;
  * the ingest plan existed but only the hosted MCP could reach it, until one REST route was
    added for the rest;
  * the hosted MCP hashed raw `source` while the apply path hashed `source.strip()`, so tokens
    it minted could never validate.

Each was invisible to unit tests, because each surface's own tests passed. What nothing checked
was that the surfaces AGREE. These tests do exactly that, and they are deliberately DB-free —
`ingest_payload` and `build_plan` are captured, never executed — so they run everywhere and stay
fast enough that nobody is tempted to skip them.

Adding a surface? Add it to SURFACES. A surface not listed here is a surface nothing compares.
"""
from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN"),
    reason="needs OPENBRAIN_TOOL_ACCESS_TOKEN to pass surface auth",
)

# The fields a caller must be able to get through to the backend. Every one of these has been
# silently dropped by at least one surface at some point.
CARRIED = ("system", "component", "valid_from",
           "plan_token", "acknowledged_not_updating", "decline_reason")

INPUT: dict[str, Any] = {
    "source_type": "text",
    "source": "conformance probe",
    "subject": "conformance",
    "topic": "surface_parity",
    "system": "SpectreNet",
    "component": "conformance-probe",
    "valid_from": "2026-08-01",
    "plan_token": "token-placeholder",
    "acknowledged_not_updating": ["some-living-doc"],
    "decline_reason": "conformance probe, not an update",
}


def _auth() -> dict[str, str]:
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['OPENBRAIN_TOOL_ACCESS_TOKEN']}"}


# (label, module attribute to patch, request builder)
SURFACES = [
    ("rest /api/ingest", "api.ingest", lambda: {
        "path": "/api/ingest", "method": "POST", "headers": _auth(),
        "body": json.dumps(INPUT)}),
    ("claude action /claude_ingest", "api.claude", lambda: {
        "path": "/claude_ingest", "method": "POST", "headers": _auth(),
        "body": json.dumps({"type": "tool_use", "name": "claude_ingest", "input": INPUT})}),
    ("chatgpt action /openbrain_ingest", "api.chatgpt", lambda: {
        "path": "/openbrain_ingest", "method": "POST", "headers": _auth(),
        "body": json.dumps({"tool_input": INPUT})}),
    ("hosted mcp tools/call ingest", "api.mcp_http", lambda: {
        "path": "/mcp/messages", "method": "POST", "headers": _auth(),
        "body": json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                            "params": {"name": "ingest", "arguments": INPUT}})}),
]


def _drive(module: str, request: dict[str, Any]) -> dict[str, Any]:
    """Run one surface with its ingest captured. Returns the payload the backend WOULD receive."""
    import importlib

    from api.app import handler

    captured: dict[str, Any] = {}

    def _capture(payload, metadata):
        captured.update(payload)
        return 200, {"status": "accepted", "message": "captured"}

    with patch.object(importlib.import_module(module), "ingest_payload", _capture):
        handler(request)
    return captured


@pytest.mark.parametrize("label,module,build", SURFACES, ids=[s[0] for s in SURFACES])
def test_every_surface_forwards_the_gate_fields(label, module, build):
    """A surface that drops one of these looks like a successful ingest and isn't."""
    captured = _drive(module, build())
    assert captured, f"{label}: nothing reached ingest_payload"
    missing = [f for f in CARRIED if f not in captured]
    assert not missing, f"{label} dropped {missing} — advertised by the spec, never forwarded"


@pytest.mark.parametrize("label,module,build", SURFACES, ids=[s[0] for s in SURFACES])
def test_every_surface_preserves_field_values(label, module, build):
    """Forwarding the key but mangling the value is the same bug wearing a hat."""
    captured = _drive(module, build())
    for field in CARRIED:
        assert captured.get(field) == INPUT[field], (
            f"{label}: {field} arrived as {captured.get(field)!r}, sent {INPUT[field]!r}")


def test_all_surfaces_agree_on_the_forwarded_set():
    """Not just 'each forwards the fields' — that they forward the SAME set as each other."""
    seen = {label: set(_drive(module, build())) & set(CARRIED)
            for label, module, build in SURFACES}
    distinct = {frozenset(v) for v in seen.values()}
    assert len(distinct) == 1, f"surfaces disagree on what they forward: {seen}"


# --- plan-token normalization -------------------------------------------------------------------
# The apply path hashes `source.strip()`. Any surface that mints a plan over a DIFFERENT
# normalization issues tokens that cannot validate — which is precisely what the hosted MCP did
# with raw source, invisible until enforcement was switched on.

PLAN_SURFACES = [
    ("rest /api/plan_ingest", lambda: {
        "path": "/api/plan_ingest", "method": "POST", "headers": _auth(),
        "body": json.dumps({"source": "  padded probe\n"})}),
    ("hosted mcp tools/call plan_ingest", lambda: {
        "path": "/mcp/messages", "method": "POST", "headers": _auth(),
        "body": json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                            "params": {"name": "plan_ingest",
                                       "arguments": {"source": "  padded probe\n"}}})}),
]


def _planned_content(request: dict[str, Any]) -> str | None:
    """The exact string a surface hands to build_plan — i.e. what its token is bound to."""
    import api.ingest_plan as ip

    from api.app import handler

    seen: list[str] = []

    def _capture(content, owner, **kwargs):
        seen.append(content)
        return {"plan_token": "x", "candidates": [], "expires_in": 600,
                "current_state": {"living_docs_in_system": [], "similar_living_docs": []}}

    with patch.object(ip, "build_plan", _capture):
        handler(request)
    return seen[0] if seen else None


def test_plan_surfaces_normalize_source_identically():
    got = {label: _planned_content(build()) for label, build in PLAN_SURFACES}
    assert None not in got.values(), f"a plan surface never reached build_plan: {got}"
    assert len(set(got.values())) == 1, (
        f"plan surfaces hash different content, so their tokens cannot both validate: {got}")


def test_plan_content_matches_what_apply_will_hash():
    """Pin the normalization to the apply path's, not merely to each other's."""
    raw = "  padded probe\n"
    for label, build in PLAN_SURFACES:
        assert _planned_content(build()) == raw.strip(), (
            f"{label} binds its token to a different string than apply hashes")
