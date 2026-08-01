"""ADR-018 P5 — as-of (valid-time) queries.

Answers "what did we hold as true at time T", using valid-time (valid_from/valid_until), NOT
status and NOT system-time. A row superseded today but valid at T still surfaces — that is the
whole point of preserving history with real lifespans. Orthogonal to the retrieval read path
(no vector search): this is a point-in-time state lookup over metadata + content.
"""
from __future__ import annotations

from typing import Any

from api._openbrain_api import get_db_conn


def as_of(when: str, *, system: str | None = None, component_key: str | None = None,
          domain: str | None = None, limit: int = 50, conn: Any = None) -> list[dict[str, Any]]:
    """Rows whose valid-time span contains `when` (valid_from <= when < valid_until, NULL = open).
    Read-only. `when` is an ISO-8601 timestamp/date string."""
    clauses = ["valid_from <= %s", "(valid_until IS NULL OR valid_until > %s)"]
    params: list[Any] = [when, when]
    for col, val in (("system", system), ("component_key", component_key), ("domain", domain)):
        if val:
            clauses.append(f"{col} = %s")
            params.append(val)
    params.append(limit)
    sql = (
        "SELECT id, status, system, component_key, domain, valid_from, valid_until, "
        "left(content, 120) AS head FROM public.knowledge "
        f"WHERE {' AND '.join(clauses)} ORDER BY valid_from DESC LIMIT %s"
    )
    own = conn is None
    conn = conn or get_db_conn()
    try:
        return [
            {
                "id": str(r["id"]), "status": r["status"], "system": r["system"],
                "component_key": r["component_key"], "domain": r["domain"],
                "valid_from": r["valid_from"].isoformat() if r["valid_from"] else None,
                "valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
                "head": r["head"],
            }
            for r in conn.execute(sql, params).fetchall()
        ]
    finally:
        if own:
            conn.close()
