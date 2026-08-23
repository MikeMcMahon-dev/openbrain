"""The retirement airlock must be REACHABLE, and must only ever target your own rows.

Migration 012 existed to fix "every surface can CREATE; none can CLOSE" — then shipped with
`propose_retirement` having exactly one reference in the repo: its own `def`. No route, no tool,
no spec. The airlock was unreachable by any agent, so nothing it promised actually changed. That
is the ADR-019 signature failure, and these tests are what stop it recurring.

The owner check is the other half. `target_id` is caller-supplied and the vault is multi-owner
(Mike, Annie, Beth), so an unscoped proposal let any surface name any row — and the rejection
handed that row's title, tags and taxonomy back in `evidence`.
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

ARGS = {
    "target_id": "11111111-2222-3333-4444-555555555555",
    "rationale": "A rationale comfortably past the twenty character minimum.",
    "method": "retire",
    "reason_code": "manual",
}


def _auth() -> dict[str, str]:
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['OPENBRAIN_TOOL_ACCESS_TOKEN']}"}


SURFACES = [
    ("rest", lambda: {"path": "/api/propose_retirement", "method": "POST", "headers": _auth(),
                      "body": json.dumps(ARGS)}),
    ("claude action", lambda: {"path": "/claude_propose_retirement", "method": "POST",
                               "headers": _auth(),
                               "body": json.dumps({"type": "tool_use", "input": ARGS})}),
    ("chatgpt action", lambda: {"path": "/openbrain_propose_retirement", "method": "POST",
                                "headers": _auth(),
                                "body": json.dumps({"tool_input": ARGS})}),
    ("hosted mcp", lambda: {"path": "/mcp/messages", "method": "POST", "headers": _auth(),
                            "body": json.dumps({"jsonrpc": "2.0", "method": "tools/call", "id": 1,
                                                "params": {"name": "propose_retirement",
                                                           "arguments": ARGS}})}),
]


def _drive(request: dict[str, Any]) -> dict[str, Any]:
    """Run a surface with propose_retirement captured; returns the kwargs it would receive."""
    import api.retirement_request as rr
    from api.app import handler

    seen: dict[str, Any] = {}

    def _capture(target_id, **kwargs):
        seen.update({"target_id": target_id, **kwargs})
        return {"status": "queued", "code": 202, "request_id": "x"}

    with patch.object(rr, "propose_retirement", _capture):
        handler(request)
    return seen


@pytest.mark.parametrize("label,build", SURFACES, ids=[s[0] for s in SURFACES])
def test_every_surface_reaches_the_airlock(label, build):
    """A surface that cannot reach it is a capability that does not exist (ADR-019)."""
    seen = _drive(build())
    assert seen, f"{label}: request never reached propose_retirement"
    assert seen["target_id"] == ARGS["target_id"]
    assert seen["method"] == "retire"
    assert seen["reason_code"] == "manual"
    assert seen["rationale"] == ARGS["rationale"]


def test_surfaces_agree_on_what_they_forward():
    seen = {label: set(_drive(build())) for label, build in SURFACES}
    assert len({frozenset(v) for v in seen.values()}) == 1, f"surfaces disagree: {seen}"


@pytest.mark.parametrize("label,build", SURFACES, ids=[s[0] for s in SURFACES])
def test_requester_is_the_authenticated_owner_not_the_payload(label, build):
    """requested_by must come from auth. If a surface let the body set it, the owner check
    below would be trivially bypassable by claiming to be someone else."""
    request = build()
    body = json.loads(request["body"])
    for envelope in ("input", "tool_input"):
        if isinstance(body.get(envelope), dict):
            body[envelope]["requested_by"] = "someone.else"
    if "params" in body:
        body["params"]["arguments"]["requested_by"] = "someone.else"
    if "target_id" in body:
        body["requested_by"] = "someone.else"
    request["body"] = json.dumps(body)
    seen = _drive(request)
    assert seen.get("requested_by") != "someone.else", (
        f"{label}: caller-supplied requested_by was honoured — the owner check is bypassable")


# --- the owner check itself --------------------------------------------------------------------

def _propose_against(evidence: dict[str, Any] | None, requester: str = "mike.mcmahon67"):
    import api.retirement_request as rr
    with patch.object(rr, "collect_evidence", lambda conn, tid: evidence or {}), \
         patch.object(rr, "get_db_conn", _fake_conn):
        return rr.propose_retirement(ARGS["target_id"], rationale=ARGS["rationale"],
                                     requested_by=requester)


class _FakeConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k): raise AssertionError("must reject before touching the database")


def _fake_conn():
    return _FakeConn()


def test_cannot_propose_removal_of_someone_elses_row():
    other = {"id": ARGS["target_id"], "created_by": "anneliesepaige", "status": "current",
             "title": "Annie's private note", "tags": ["Study"], "hard_delete_legal": True}
    result = _propose_against(other, requester="mike.mcmahon67")
    assert result["status"] == "rejected"
    assert "evidence" not in result, "the rejection leaked the row it refused to act on"
    assert "Annie" not in json.dumps(result), "the rejection leaked the row's content"


def test_missing_and_not_yours_are_indistinguishable():
    """Different messages would make this an existence oracle for other people's ids."""
    missing = _propose_against(None)
    not_yours = _propose_against({"id": ARGS["target_id"], "created_by": "someone.else",
                                  "hard_delete_legal": True})
    assert missing["message"] == not_yours["message"]
