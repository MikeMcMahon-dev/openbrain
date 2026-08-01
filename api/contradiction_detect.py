"""ADR-018 P4 — contradiction candidate detection + human-review actions.

Surfaces same-system, high-similarity `current` pairs as CANDIDATES (ADR §7). It does NOT judge:
two similar rows may both be true. A human confirms (→ a `contradiction_confirmed` supersession
event on the P3 rails, retiring the loser) or dismisses (→ nothing written, and the pair is
remembered so it is never re-flagged).

Threshold defaults to 0.85 (the P0 baseline: 9 same-system pairs today) and is env-overridable
via OPENBRAIN_CONTRADICTION_SIM_THRESHOLD so it can be tuned without a redeploy.
"""
from __future__ import annotations

import os
from typing import Any

from api._openbrain_api import get_db_conn


def _threshold(explicit: float | None = None) -> float:
    if explicit is not None:
        return explicit
    return float(os.getenv("OPENBRAIN_CONTRADICTION_SIM_THRESHOLD", "0.85"))


# Same-system current pairs, canonical order (id_lo < id_hi), cosine similarity >= threshold.
# ON CONFLICT DO NOTHING is the dismissal memory: a pair already recorded (pending, confirmed,
# OR dismissed) is left untouched, so a dismissed pair is never re-surfaced.
_DETECT_SQL = """
INSERT INTO public.contradiction_candidates (id_lo, id_hi, system, similarity)
SELECT a.id, b.id, a.system, (1 - (a.embedding <=> b.embedding))::real
FROM public.knowledge a
JOIN public.knowledge b ON a.system = b.system AND a.id < b.id
WHERE a.status = 'current' AND b.status = 'current'
  AND a.system IS NOT NULL
  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
  AND (1 - (a.embedding <=> b.embedding)) >= %s
ON CONFLICT (id_lo, id_hi) DO NOTHING
"""


def detect_candidates(conn: Any = None, threshold: float | None = None) -> dict[str, Any]:
    """Insert new pending candidate pairs at/above the similarity threshold. Idempotent — never
    re-flags a dismissed or confirmed pair. Returns {inserted, pending, threshold}."""
    thr = _threshold(threshold)
    own = conn is None
    conn = conn or get_db_conn()
    try:
        inserted = conn.execute(_DETECT_SQL, [thr]).rowcount
        pending = conn.execute(
            "SELECT count(*) AS n FROM public.contradiction_candidates WHERE status='pending'"
        ).fetchone()["n"]
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()
    return {"inserted": inserted, "pending": pending, "threshold": thr}


def list_pending(conn: Any = None) -> list[dict[str, Any]]:
    """Pending pairs where BOTH sides are still current, strongest first, with content heads.
    A pair whose side has since been retired by another path is moot and is filtered out."""
    own = conn is None
    conn = conn or get_db_conn()
    try:
        rows = conn.execute(
            """
            SELECT c.id AS pair_id, c.system, c.similarity,
                   c.id_lo, left(klo.content, 100) AS lo_head,
                   c.id_hi, left(khi.content, 100) AS hi_head
            FROM public.contradiction_candidates c
            JOIN public.knowledge klo ON klo.id = c.id_lo
            JOIN public.knowledge khi ON khi.id = c.id_hi
            WHERE c.status = 'pending'
              AND klo.status = 'current' AND khi.status = 'current'
            ORDER BY c.similarity DESC
            """
        ).fetchall()
        return [
            {
                "pair_id": str(r["pair_id"]), "system": r["system"],
                "similarity": round(float(r["similarity"]), 4),
                "id_lo": str(r["id_lo"]), "lo_head": r["lo_head"],
                "id_hi": str(r["id_hi"]), "hi_head": r["hi_head"],
            }
            for r in rows
        ]
    finally:
        if own:
            conn.close()


def _confirm(conn: Any, pair_id: str, loser_id: str, actor: str,
             note: str | None, fact_valid_until: str | None) -> dict[str, Any]:
    cand = conn.execute(
        "SELECT id_lo, id_hi, status FROM public.contradiction_candidates WHERE id = %s",
        [pair_id],
    ).fetchone()
    if not cand:
        return {"ok": False, "error": "pair not found"}
    if cand["status"] != "pending":
        return {"ok": False, "error": f"pair is already {cand['status']}"}
    sides = {str(cand["id_lo"]), str(cand["id_hi"])}
    if loser_id not in sides:
        return {"ok": False, "error": "loser_id is not a member of the pair"}
    winner_id = (sides - {loser_id}).pop()

    states = {
        str(r["id"]): r["status"]
        for r in conn.execute(
            "SELECT id, status FROM public.knowledge WHERE id = ANY(%s)",
            [[loser_id, winner_id]],
        ).fetchall()
    }
    if states.get(loser_id) != "current" or states.get(winner_id) != "current":
        return {"ok": False, "error": "both rows must still be current to confirm"}

    # Retire the loser via a contradiction_confirmed event; the P3 projection flips its status.
    # Optional backdated fact-offset (P5): when the loser stopped being true earlier than now.
    conn.execute(
        """
        INSERT INTO public.supersession_events
            (superseded_id, superseding_id, occurred_at, fact_valid_until,
             reason_code, reason_note, actor, method)
        VALUES (%s, %s, now(), %s, 'contradiction_confirmed', %s, %s, 'human')
        """,
        [loser_id, winner_id, fact_valid_until, note, actor],
    )
    conn.execute(
        """
        UPDATE public.contradiction_candidates
        SET status = 'confirmed', reviewed_at = now(), reviewer = %s, note = %s
        WHERE id = %s
        """,
        [actor, note, pair_id],
    )
    return {"ok": True, "pair_id": pair_id, "loser_id": loser_id, "winner_id": winner_id}


def confirm(pair_id: str, loser_id: str, actor: str, note: str | None = None,
            fact_valid_until: str | None = None, conn: Any = None) -> dict[str, Any]:
    """Confirm a pair as a real contradiction: retire `loser_id` (a `contradiction_confirmed`
    event; the projection sets it superseded), keep the other. Marks the candidate confirmed.
    `fact_valid_until` optionally backdates when the loser stopped being true (P5)."""
    if conn is not None:
        return _confirm(conn, pair_id, loser_id, actor, note, fact_valid_until)
    with get_db_conn() as c:
        return _confirm(c, pair_id, loser_id, actor, note, fact_valid_until)


def _dismiss(conn: Any, pair_id: str, actor: str, note: str | None) -> dict[str, Any]:
    row = conn.execute(
        """
        UPDATE public.contradiction_candidates
        SET status = 'dismissed', reviewed_at = now(), reviewer = %s, note = %s
        WHERE id = %s AND status = 'pending'
        RETURNING id
        """,
        [actor, note, pair_id],
    ).fetchone()
    if not row:
        return {"ok": False, "error": "pair not found or not pending"}
    return {"ok": True, "pair_id": pair_id}


def dismiss(pair_id: str, actor: str, note: str | None = None, conn: Any = None) -> dict[str, Any]:
    """Dismiss a pair (both rows may be true). Writes NO event; the pair is remembered so
    detection never re-flags it."""
    if conn is not None:
        return _dismiss(conn, pair_id, actor, note)
    with get_db_conn() as c:
        return _dismiss(c, pair_id, actor, note)
