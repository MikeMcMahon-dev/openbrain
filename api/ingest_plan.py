"""ingest_plan.py — plan/apply handshake for ingest ("terraform plan" for the vault).

THE PROBLEM THIS SOLVES

Deciding whether a note is a NEW record or an UPDATE to an existing living doc requires knowing
what living docs already exist. No surface could enumerate them — `query`/`search`/`fetch` return
content, never an index of `component_key`s — so every writer has been guessing from conversational
memory. Twice that produced a keyless row competing with the living doc it should have superseded
(`400a4e85`, `997ce045`), and both times the ingest returned 200.

Similarity cannot close this. Measured over 228 keyless current rows against every component-keyed
current doc: the one true mis-filed update scored 0.810 and ranked FOURTH, below three legitimate
DNS session wraps at 0.834 / 0.823 / 0.815. A gate at 0.80 fires on 5 rows of which 4 are correct
notes. Topic similarity measures SUBJECT; the update-vs-note distinction is INTENT, which is not in
the content. So the plan SHOWS similar docs as suggestions (free when a writer picks from a list)
and never decides from them.

THE HANDSHAKE

    plan  -> current_state + would_supersede + plan_token   (writes nothing)
    apply -> ingest WITH the token and an explicit answer

The token is an HMAC over (content_hash, owner, exp) — the same stateless pattern as the OAuth
codes in api/oauth.py, so there is no new table and no new crypto. Binding the hash is what makes
it a plan FILE rather than a plan flag: you cannot preview one document and commit another.

WHAT THIS DOES AND DOES NOT GUARANTEE

It structurally prevents IGNORANCE — a writer cannot commit without having been shown what exists.
That is the failure that actually occurred, both times.

It cannot prevent DEFIANCE. An agent that reads the plan and still declares "not an update" will
write a keyless row. No mechanism short of removing write access changes that. What the design does
instead is make the decision explicit, attributable, and reviewable:

  * declining is not a boolean — the writer must NAME the docs it is declining to update, so "nah"
    requires stating the thing being ignored rather than skipping a field;
  * declining a high-similarity candidate additionally requires a written reason;
  * every decline is recorded in the returned receipt for later audit.

Ignorance is now impossible and defiance now leaves a record. That is the honest ceiling.

SHIPS DARK: enforcement is off unless OPENBRAIN_REQUIRE_INGEST_PLAN is set, matching how the
component boost, chunk-on-ingest, and recency net all shipped behaviour-neutral.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any

PLAN_TTL_SECONDS = 600
# Above this similarity, declining to update the candidate requires a written reason. Set from the
# measured distribution: p75 of keyless-vs-living-doc similarity is 0.518 and the top of the range
# is 0.834, so 0.75 selects the handful of genuinely-close cases without taxing ordinary notes.
DECLINE_REASON_THRESHOLD = 0.75
# Floor for the SUGGESTION channel. Without it the top-3 includes anything, and a live run put
# `vlan-switch-topology` at 0.307 into the must-acknowledge set for a flight-sim note — noise the
# writer is then forced to name. Junk friction is how a gate gets switched off. 0.50 sits just
# under the measured p75 (0.518) so ordinary notes stay clean while a genuine missing-system case
# still surfaces (the 997ce045 body scores 0.576 against flightsim-hardware).
SIMILAR_SUGGEST_FLOOR = 0.50


def enforcement_enabled() -> bool:
    return (os.getenv("OPENBRAIN_REQUIRE_INGEST_PLAN") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _signing_secret() -> bytes:
    return os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "openbrain-plan-secret").encode()


def content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def encode_plan_token(chash: str, owner: str) -> str:
    payload = json.dumps({"h": chash, "o": owner, "exp": int(time.time()) + PLAN_TTL_SECONDS},
                         separators=(",", ":"))
    b64 = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    sig = hmac.new(_signing_secret(), b64.encode(), hashlib.sha256).hexdigest()
    return f"{b64}.{sig}"


def decode_plan_token(token: str, chash: str, owner: str) -> tuple[bool, str]:
    """Verify a plan token binds to THIS content and owner and has not expired."""
    try:
        b64, sig = str(token).split(".", 1)
    except (ValueError, AttributeError):
        return False, "malformed plan_token"
    expected = hmac.new(_signing_secret(), b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, "plan_token signature invalid"
    try:
        data = json.loads(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)))
    except Exception:
        return False, "plan_token payload unreadable"
    if int(data.get("exp", 0)) < int(time.time()):
        return False, "plan_token expired — re-run the plan"
    if data.get("o") != owner:
        return False, "plan_token was issued to a different owner"
    if data.get("h") != chash:
        return False, ("plan_token does not match this content — you planned one document and "
                       "tried to commit another. Re-run the plan on the exact content.")
    return True, "ok"


def _living_docs(conn, system: str | None) -> list[dict[str, Any]]:
    """Every living doc under `system` — structural and exact, not a guess."""
    if not system:
        return []
    rows = conn.execute(
        # ::int / ::float casts are load-bearing, not cosmetic. Postgres `round()` over
        # `extract(epoch ...)` returns numeric, which psycopg maps to Decimal, and Decimal is not
        # JSON-serializable — so the plan built fine in Python and then 500'd the moment an MCP
        # surface tried to serialize it. Cast at the source so no caller has to remember.
        """SELECT id::text, system, component_key,
                  round(extract(epoch FROM (now() - created_at)) / 86400)::int AS age_days,
                  split_part(regexp_replace(content, '^#+\\s*', ''), E'\\n', 1) AS title
             FROM public.knowledge
            WHERE system = %s AND component_key IS NOT NULL AND status = 'current'
            ORDER BY component_key""", [system]).fetchall()
    return [dict(r) for r in rows]


def _similar_docs(conn, embedding_sql_param, limit: int = 3) -> list[dict[str, Any]]:
    """Closest component-keyed current docs regardless of system.

    This is a SUGGESTION channel, never a decision: it exists to catch a missing or wrong `system`
    (997ce045 sent system=NULL and this surfaces flightsim-hardware at rank 1). Precision is poor
    by construction — see the module docstring — which is fine when a writer picks from a list.
    """
    if embedding_sql_param is None:
        return []
    rows = conn.execute(
        # ::float for the same reason as _living_docs — see the note there.
        """SELECT id::text, system, component_key,
                  (1 - (embedding <=> %s::vector))::float AS similarity,
                  split_part(regexp_replace(content, '^#+\\s*', ''), E'\\n', 1) AS title
             FROM public.knowledge
            WHERE component_key IS NOT NULL AND status = 'current' AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector LIMIT %s""",
        [embedding_sql_param, embedding_sql_param, limit]).fetchall()
    return [dict(r) for r in rows if (r["similarity"] or 0) >= SIMILAR_SUGGEST_FLOOR]


def build_plan(content: str, owner: str, *, system: str | None = None,
               component: str | None = None) -> dict[str, Any]:
    """Read-only preview. Writes nothing. Returns current state, blast radius, and a token."""
    from api._openbrain_api import get_db_conn

    chash = content_hash(content)
    embedding_param = None
    try:
        from api._openbrain_api import _vector_param, embedding_request
        emb = embedding_request(content)
        if emb:
            embedding_param = _vector_param(emb)
    except Exception:
        embedding_param = None  # suggestions degrade to empty; structural list still works

    with get_db_conn() as conn:
        living = _living_docs(conn, system)
        similar = _similar_docs(conn, embedding_param)

        would_supersede = None
        if system and component:
            row = conn.execute(
                """SELECT id::text, component_key,
                          split_part(regexp_replace(content,'^#+\\s*',''), E'\\n',1) AS title
                     FROM public.knowledge
                    WHERE system = %s AND component_key = %s AND status = 'current'""",
                [system, component]).fetchone()
            would_supersede = dict(row) if row else None

    known = {d["component_key"] for d in living} | {d["component_key"] for d in similar}
    return {
        "plan_token": encode_plan_token(chash, owner),
        "expires_in": PLAN_TTL_SECONDS,
        "content_hash": chash,
        "declared": {"system": system, "component": component},
        "current_state": {
            "living_docs_in_system": living,
            "similar_living_docs": similar,
            "_note": ("similar_living_docs is a suggestion channel only — topic similarity does "
                      "not distinguish an update from a note. Use it to catch a missing system."),
        },
        "would_supersede": would_supersede,
        "decline_reason_threshold": DECLINE_REASON_THRESHOLD,
        "decision_required": (
            "Commit with EITHER component=<one of the above> to update that living doc, OR "
            "acknowledged_not_updating=[<every component you were shown>] to write an "
            "append-only note. There is no way to skip this question."
            if known else
            "No living docs are in scope. Commit as an append-only note."
        ),
        "candidates": sorted(known),
    }


def verify_apply(payload: dict[str, Any], owner: str, content: str) -> tuple[bool, dict[str, Any]]:
    """Gate an apply. Returns (ok, error_body). Only enforced for gated owners."""
    chash = content_hash(content)
    token = payload.get("plan_token")

    if not token:
        plan = build_plan(content, owner,
                          system=payload.get("system"), component=payload.get("component"))
        return False, {
            "error": "plan_required",
            "status": 409,
            "message": ("This ingest requires a plan first. The plan below shows what already "
                        "exists; re-send with its plan_token and an explicit decision."),
            "plan": plan,
        }

    ok, reason = decode_plan_token(token, chash, owner)
    if not ok:
        return False, {"error": "plan_token_invalid", "status": 409, "message": reason}

    # An update names its target; the schema already requires system alongside component.
    if payload.get("component"):
        return True, {}

    # Declining is not a boolean. Name what you are declining, so "nah" has to be stated.
    ack = payload.get("acknowledged_not_updating")
    ack = [str(a) for a in ack] if isinstance(ack, list) else []
    plan = build_plan(content, owner, system=payload.get("system"))
    candidates = set(plan["candidates"])
    if candidates and not candidates.issubset(set(ack)):
        return False, {
            "error": "decision_required",
            "status": 409,
            "message": ("You were shown living docs and did not declare a decision. Either set "
                        "component=<one of them>, or list EVERY candidate in "
                        "acknowledged_not_updating to record that this is an append-only note."),
            "missing": sorted(candidates - set(ack)),
            "plan": plan,
        }

    # Declining a close match additionally costs a written reason.
    close = [d for d in plan["current_state"]["similar_living_docs"]
             if (d.get("similarity") or 0) >= DECLINE_REASON_THRESHOLD]
    if close and not str(payload.get("decline_reason") or "").strip():
        return False, {
            "error": "decline_reason_required",
            "status": 409,
            "message": (f"Declining to update {close[0]['component_key']} "
                        f"(similarity {close[0]['similarity']:.3f}) requires a decline_reason. "
                        "State why this is a new record rather than an update to it."),
            "plan": plan,
        }

    return True, {}
