"""Supersession must only ever touch your own rows.

`propose_supersession` and `confirm_supersession` both took a caller-supplied row id and never
checked who owned it. The chain that opened up: propose against ANY id -> confirm -> the target
is RETIRED. That retirement is irreversible by construction — `supersession_events` is
append-only and its FK is not deferrable, so the row cannot even be deleted afterwards.

Worse than the retirement airlock's equivalent gap, because the airlock deliberately requires a
human to approve precisely BECAUSE removal is irreversible, while confirm performed an equally
irreversible retire with no approval and no owner check.

The second half is identity. `_require_tool_auth` resolves an owner only for the per-owner tokens
in OPENBRAIN_TOKEN_OWNER_MAP; the shared token authenticates without one, and these handlers then
fell back to `body["owner"]`. That made the ownership check bypassable by simply claiming to be
the row's owner, so the body must never be able to set identity.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import api.ob2_state as ob2

MINE, THEIRS = "mike.mcmahon67", "anneliesepaige"
TARGET = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
DRAFT = "11111111-2222-3333-4444-555555555555"


class _Rows:
    """A connection stub returning canned rows for the id each query asks about."""

    def __init__(self, by_id: dict[str, dict[str, Any] | None]):
        self.by_id = by_id
        self.writes: list[str] = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=None):
        if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            self.writes.append(sql)
            return self
        key = str(params[0]) if params else ""
        self._row = self.by_id.get(key)
        return self

    def fetchone(self): return self._row


def _request(body: dict[str, Any]) -> dict[str, Any]:
    return {"path": "/api/propose_supersession", "method": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body)}


def _run(handler, body, rows, resolved_owner=MINE):
    conn = _Rows(rows)
    with patch.object(ob2, "require_auth_owner", lambda m: (None, resolved_owner)), \
         patch.object(ob2, "get_db_conn", lambda: conn):
        response = handler(_request(body))
    return response, conn


PROPOSE_BODY = {"supersedes_id": TARGET, "content": "Replacement content for the living doc.",
                "domain": "OpenBrain", "environment": "Production"}


def test_cannot_propose_superseding_someone_elses_row():
    theirs = {"id": TARGET, "status": "current", "created_by": THEIRS}
    response, conn = _run(ob2.handle_propose_supersession, PROPOSE_BODY, {TARGET: theirs})
    assert response["statusCode"] == 404
    assert not conn.writes, "a draft was written against a row the caller does not own"


def test_not_found_and_not_yours_are_indistinguishable():
    theirs = {"id": TARGET, "status": "current", "created_by": THEIRS}
    not_yours, _ = _run(ob2.handle_propose_supersession, PROPOSE_BODY, {TARGET: theirs})
    missing, _ = _run(ob2.handle_propose_supersession, PROPOSE_BODY, {TARGET: None})
    assert json.loads(not_yours["body"])["message"] == json.loads(missing["body"])["message"]


def test_body_cannot_set_the_caller_identity():
    """With the shared token no owner resolves; the body must not get to fill that gap."""
    theirs = {"id": TARGET, "status": "current", "created_by": THEIRS}
    body = dict(PROPOSE_BODY, owner=THEIRS)
    response, conn = _run(ob2.handle_propose_supersession, body, {TARGET: theirs},
                          resolved_owner=None)
    assert response["statusCode"] == 404, (
        "claiming to be the row's owner in the body bypassed the ownership check")
    assert not conn.writes


def test_confirm_refuses_a_draft_you_do_not_own():
    rows = {DRAFT: {"id": DRAFT, "status": "draft", "supersedes_id": TARGET,
                    "domain": "OpenBrain", "system": "OpenBrain", "created_by": THEIRS},
            TARGET: {"id": TARGET, "status": "current", "created_by": THEIRS}}
    response, conn = _run(ob2.handle_confirm_supersession, {"proposal_id": DRAFT}, rows)
    assert response["statusCode"] == 404
    assert not conn.writes, "an irreversible retirement event was written for another owner's row"


def test_confirm_refuses_when_only_the_target_is_someone_elses():
    """A draft you own must not be usable to retire a row you do not."""
    rows = {DRAFT: {"id": DRAFT, "status": "draft", "supersedes_id": TARGET,
                    "domain": "OpenBrain", "system": "OpenBrain", "created_by": MINE},
            TARGET: {"id": TARGET, "status": "current", "created_by": THEIRS}}
    response, conn = _run(ob2.handle_confirm_supersession, {"proposal_id": DRAFT}, rows)
    assert response["statusCode"] == 404
    assert not conn.writes


def test_your_own_row_still_passes_the_ownership_gate():
    """The check must not break the legitimate path — it should get past ownership to the work."""
    mine = {"id": TARGET, "status": "current", "created_by": MINE}
    with patch.object(ob2, "write_knowledge", create=True):
        response, _ = _run(ob2.handle_propose_supersession, PROPOSE_BODY, {TARGET: mine})
    assert response["statusCode"] != 404, "a row the caller owns was refused as not-found"
