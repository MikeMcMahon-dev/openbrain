"""retirement_request.py — the agent-facing half of the removal airlock (migration 012).

An agent may PROPOSE that a row be removed. It may not remove one. This module is the only
write path an agent gets, and everything it writes lands as `status='pending'` for a human.

Why an airlock rather than a retire tool: removal is the sole irreversible operation in the
vault. The supersession log is append-only and status is a projection, so a `retire` is
recoverable — but a hard `delete` is not, and an agent cannot reliably tell which of the two a
given row deserves. So the agent supplies the target, a rationale, and machine-checked evidence;
the human supplies the judgement.

Two refusals are built in, and both matter more than they look:

  * ALREADY DENIED — a denied (target, method) is never re-proposed. Without this an agent
    re-surfaces the same rejected removal every pass, and review fatigue ends up doing the
    deleting. The denial IS the memory.
  * OPEN REQUEST — one pending/approved request per (target, method), enforced by a partial
    unique index, so a retry storm cannot queue the same removal fifty times.
  * NOT YOURS — a caller may only name rows it owns. target_id is caller-supplied and the vault
    is multi-owner, so an unscoped proposal let any surface name any row and read its title and
    taxonomy back out of the rejection. Not-yours and not-found answer identically, on purpose.

Evidence is captured at request time and re-checked at execution time; see
`scripts/retirement_review.py`, which refuses to execute on drift.
"""
from __future__ import annotations

from typing import Any

from api.knowledge_ingest import get_db_conn

VALID_METHODS = ("retire", "delete")
VALID_REASONS = ("explicit", "component_collision", "contradiction_confirmed",
                 "ttl_expiry", "manual", "migration")
MIN_RATIONALE = 20


def _reject(message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "rejected", "code": 400, "error": "bad_request",
            "message": message, **extra}


def collect_evidence(conn, target_id: str) -> dict[str, Any]:
    """Machine-checkable facts about the target, captured now.

    These are the numbers that decide whether a `delete` is even legal: a row referenced by an
    immutable supersession event cannot be hard-deleted, because that FK is not deferrable.
    """
    row = conn.execute(
        """SELECT k.id::text, k.status, k.domain, k.environment, k.system,
                  k.component_key, k.tags, k.created_by,
                  round(extract(epoch FROM (now() - k.created_at)) / 86400) AS age_days,
                  length(k.content) AS content_len,
                  split_part(regexp_replace(k.content, '^#+\\s*', ''), E'\\n', 1) AS title,
                  (SELECT count(*) FROM public.supersession_events e
                    WHERE e.superseded_id = k.id OR e.superseding_id = k.id) AS ref_events,
                  (SELECT count(*) FROM public.knowledge p
                    WHERE p.supersedes_id = k.id) AS ref_parents,
                  (SELECT count(*) FROM public.contradiction_candidates cc
                    WHERE cc.id_lo = k.id OR cc.id_hi = k.id) AS ref_contradictions,
                  (SELECT count(*) FROM public.knowledge_chunked kc
                    WHERE kc.document_id = k.id) AS chunks
             FROM public.knowledge k WHERE k.id = %s""", [target_id]).fetchone()
    if row is None:
        return {}
    ev = dict(row)
    ev["hard_delete_legal"] = (
        ev["ref_events"] == 0 and ev["ref_parents"] == 0 and ev["ref_contradictions"] == 0
    )
    return ev


def propose_retirement(
    target_id: str,
    *,
    rationale: str,
    requested_by: str,
    method: str = "retire",
    reason_code: str = "manual",
) -> dict[str, Any]:
    """Queue a removal for human review. Never removes anything itself."""
    if method not in VALID_METHODS:
        return _reject(f"method must be one of {VALID_METHODS}")
    if reason_code not in VALID_REASONS:
        return _reject(f"reason_code must be one of {VALID_REASONS}")
    rationale = (rationale or "").strip()
    if len(rationale) < MIN_RATIONALE:
        return _reject(
            f"rationale must be at least {MIN_RATIONALE} characters — state WHY this row should "
            "go. A request a human cannot evaluate is a request that should be denied."
        )

    with get_db_conn() as conn:
        evidence = collect_evidence(conn, target_id)

        # You may only propose removal of your OWN rows. This vault is multi-owner (Mike, Annie,
        # Beth) and target_id is caller-supplied, so without this any surface could name any row
        # — and the rejection used to hand back that row's title, tags and taxonomy in `evidence`.
        # Missing and not-yours deliberately return the SAME message: distinguishing them turns
        # this into an existence oracle for other people's ids.
        if not evidence or evidence.get("created_by") != requested_by:
            return _reject(
                "target_id not found among your rows in public.knowledge. You may only propose "
                "removal of content you own.",
                target_id=target_id,
            )

        denied = conn.execute(
            """SELECT id::text, decision_note, decided_at FROM public.retirement_requests
                WHERE target_id = %s AND method = %s AND status = 'denied'
                ORDER BY decided_at DESC LIMIT 1""", [target_id, method]).fetchone()
        if denied:
            return _reject(
                "this removal was already denied — do not re-propose it. If circumstances have "
                "genuinely changed, say so to the owner directly rather than re-queuing.",
                previously_denied=dict(denied))

        if method == "delete" and not evidence["hard_delete_legal"]:
            return _reject(
                "hard delete is not legal for this row: it is referenced by an immutable "
                "supersession event or another row. Propose method='retire' instead.",
                evidence=evidence)

        try:
            new = conn.execute(
                """INSERT INTO public.retirement_requests
                     (target_id, method, reason_code, rationale, evidence, requested_by)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   RETURNING id::text, status, requested_at""",
                [target_id, method, reason_code, rationale,
                 __import__("json").dumps(evidence, default=str), requested_by]).fetchone()
        except Exception as exc:  # unique-violation on the open-request index lands here
            if "one_open_request_per_target" in str(exc):
                return _reject(
                    "an open request already exists for this target and method — wait for it to "
                    "be decided rather than queuing another.", target_id=target_id)
            return _reject(f"could not queue request: {exc}")

    return {
        "status": "queued",
        "code": 202,
        "request_id": new["id"],
        "target_id": target_id,
        "method": method,
        "message": ("Queued for human approval. Nothing has been removed. Review with "
                    "`python scripts/retirement_review.py list`."),
        "evidence": evidence,
    }
