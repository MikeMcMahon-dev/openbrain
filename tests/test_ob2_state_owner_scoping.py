"""Supersession must only ever touch your own rows.

`propose_supersession` and `confirm_supersession` both took a caller-supplied row id and never
checked who owned it. The chain that opened up: propose against ANY id -> confirm -> the target
is RETIRED. That retirement is irreversible by construction — `supersession_events` is
append-only and its FK is not deferrable, so the row cannot even be deleted afterwards.

Worse than the retirement airlock's equivalent gap, because the airlock deliberately requires a
human to approve precisely BECAUSE removal is irreversible, while confirm performed an equally
irreversible retire with no approval and no owner check.

The second half is identity, and it is DEFENCE IN DEPTH rather than a live bypass. The token
owner map is consulted before the shared-token comparison, and OPENBRAIN_TOOL_ACCESS_TOKEN is
itself one of its keys, so every real caller resolves an owner today and the old `body["owner"]`
fallback was unreachable. It becomes reachable the moment a token is issued outside the map — and
then the body would be choosing the identity the checks above are measured against. Pinned so
that cannot happen quietly.
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
    """Simulates a token that resolves no owner — not today's config, but the latent case."""
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
    """A draft you own must not be usable to retire a row you do not.

    Also pins the ORDER of the two checks. The confirm gate echoes the target's title back in
    `would_retire` so a human can see what they are about to destroy — which means running it
    before the ownership check would disclose another owner's content through the very gate added
    to make retirement safe. Ownership first, always.
    """
    rows = {DRAFT: {"id": DRAFT, "status": "draft", "supersedes_id": TARGET,
                    "domain": "OpenBrain", "system": "OpenBrain", "created_by": MINE},
            TARGET: dict(_confirm_rows(target_owner=THEIRS)[TARGET])}
    response, conn = _run(ob2.handle_confirm_supersession, {"proposal_id": DRAFT}, rows)
    assert response["statusCode"] == 404
    assert not conn.writes
    assert "SpectreNet DNS" not in response["body"], (
        "the confirm gate disclosed another owner's row title before checking ownership")


def test_your_own_row_still_passes_the_ownership_gate():
    """The check must not break the legitimate path — it should get past ownership to the work."""
    mine = {"id": TARGET, "status": "current", "created_by": MINE}
    with patch.object(ob2, "write_knowledge", create=True):
        response, _ = _run(ob2.handle_propose_supersession, PROPOSE_BODY, {TARGET: mine})
    assert response["statusCode"] != 404, "a row the caller owns was refused as not-found"


# --- confirming what you retire ------------------------------------------------------------------
# The ownership checks close CROSS-owner damage. They do nothing for the likelier failure: an agent
# holding a stale or mistaken proposal_id retiring the wrong row inside its own vault — same owner,
# check passes, row gone. And gone is final: the event log is append-only with a non-deferrable FK,
# so a mis-retired row can be neither restored nor deleted. Confirm must therefore state WHICH row
# it means, the way the retirement airlock makes a human name its target.

def _confirm_rows(target_owner=MINE):
    return {DRAFT: {"id": DRAFT, "status": "draft", "supersedes_id": TARGET,
                    "domain": "OpenBrain", "system": "OpenBrain", "created_by": MINE},
            TARGET: {"id": TARGET, "status": "current", "created_by": target_owner,
                     "title": "SpectreNet DNS — CURRENT STATE",
                     "component_key": "dns-current-state", "system": "SpectreNet"}}


def test_confirm_without_naming_the_target_is_refused():
    response, conn = _run(ob2.handle_confirm_supersession, {"proposal_id": DRAFT}, _confirm_rows())
    body = json.loads(response["body"])
    assert response["statusCode"] == 409
    assert body["error"] == "confirm_target_required"
    assert not conn.writes, "a row was retired without the caller naming it"


def test_the_refusal_names_the_row_it_would_have_retired():
    """A gate the caller cannot answer is a gate they will route around."""
    response, _ = _run(ob2.handle_confirm_supersession, {"proposal_id": DRAFT}, _confirm_rows())
    would = json.loads(response["body"])["would_retire"]
    assert would["id"] == TARGET
    assert would["title"] == "SpectreNet DNS — CURRENT STATE"
    assert would["component_key"] == "dns-current-state"


def test_confirm_with_the_wrong_target_is_refused():
    """The stale-proposal_id case: the caller names one row, the draft points at another."""
    body = {"proposal_id": DRAFT, "confirm_retires_id": "99999999-9999-9999-9999-999999999999"}
    response, conn = _run(ob2.handle_confirm_supersession, body, _confirm_rows())
    assert response["statusCode"] == 409
    assert json.loads(response["body"])["error"] == "confirm_target_required"
    assert not conn.writes


def test_confirm_with_the_matching_target_passes_the_gate():
    """The gate must not block the legitimate path."""
    body = {"proposal_id": DRAFT, "confirm_retires_id": TARGET}
    response, _ = _run(ob2.handle_confirm_supersession, body, _confirm_rows())
    err = json.loads(response["body"]).get("error")
    assert err != "confirm_target_required", "a correctly-named target was still refused"
