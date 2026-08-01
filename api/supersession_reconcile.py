"""ADR-018 P3 — supersession reconciliation.

Proves that `knowledge.status` (the stored projection) matches what the append-only
`supersession_events` log implies. Drift is a BUG, not a warning: the event log is the source
of truth and status is rebuildable from it (recovery is replay, not restore).

The projection owns the retirement transition ONLY:
  * an event with superseding_id  -> the superseded row must be status='superseded'
  * an event without superseding_id (pure expiry) -> the superseded row must be 'historical'
  * a row stored 'superseded' MUST have an event (no unrecorded retirement)

Birth-state rows are OUT of scope and never flagged: the 67 bulk-migrated 'historical' rows and
any 'draft'/'current' carry no event by design. Only 'superseded' requires an event backing.
"""
from __future__ import annotations

import json
from typing import Any

from api._openbrain_api import get_db_conn

# Every drift class in one query. Each row: (knowledge_id, stored_status, expected_status, reason).
_DRIFT_SQL = """
-- A. event implies a retirement, but the stored status doesn't match
SELECT e.superseded_id AS knowledge_id,
       k.status        AS stored_status,
       CASE WHEN e.superseding_id IS NOT NULL THEN 'superseded' ELSE 'historical' END
                       AS expected_status,
       'event_present_status_mismatch' AS reason
FROM public.supersession_events e
JOIN public.knowledge k ON k.id = e.superseded_id
WHERE k.status <> CASE WHEN e.superseding_id IS NOT NULL THEN 'superseded' ELSE 'historical' END

UNION ALL

-- B. stored 'superseded' with no event backing it (an unrecorded retirement)
SELECT k.id, k.status, 'current' AS expected_status,
       'superseded_without_event' AS reason
FROM public.knowledge k
WHERE k.status = 'superseded'
  AND NOT EXISTS (SELECT 1 FROM public.supersession_events e WHERE e.superseded_id = k.id)
"""


def reconcile_supersession(conn: Any = None) -> dict[str, Any]:
    """Return {'ok': bool, 'drift_count': int, 'drift': [ {knowledge_id, stored_status,
    expected_status, reason} ], 'events': int, 'superseded': int}. Read-only."""
    own = conn is None
    conn = conn or get_db_conn()
    try:
        drift = [
            {
                "knowledge_id": str(r["knowledge_id"]),
                "stored_status": r["stored_status"],
                "expected_status": r["expected_status"],
                "reason": r["reason"],
            }
            for r in conn.execute(_DRIFT_SQL).fetchall()
        ]
        events = conn.execute(
            "SELECT count(*) AS n FROM public.supersession_events").fetchone()["n"]
        superseded = conn.execute(
            "SELECT count(*) AS n FROM public.knowledge WHERE status = 'superseded'"
        ).fetchone()["n"]
    finally:
        if own:
            conn.close()

    return {
        "ok": len(drift) == 0,
        "drift_count": len(drift),
        "drift": drift,
        "events": events,
        "superseded": superseded,
    }


def write_reconcile_log(conn: Any, report: dict[str, Any]) -> None:
    """Append one row to supersession_reconcile_log recording a reconciliation run. The
    drift_count>0 rows are the alert surface — a Grafana Postgres datasource or email digest
    reads this later. Caller owns the connection/commit."""
    conn.execute(
        """
        INSERT INTO public.supersession_reconcile_log
            (drift_count, events, superseded, details)
        VALUES (%s, %s, %s, %s)
        """,
        [report["drift_count"], report["events"], report["superseded"],
         json.dumps(report["drift"])],
    )
