#!/usr/bin/env python3
"""Live DB integrity guardrails for the knowledge / knowledge_chunked stores.

Born from the 2026-08-06 incident post-mortem: migration 007 dropped
status/system/tags from public.knowledge_chunked but LEFT the migration-005 trigger
`validate_knowledge_chunked_insert()` in place, whose PL/pgSQL body still read
NEW.status / NEW.system / NEW.tags. Postgres does NOT record a catalog dependency from a
function body to the columns it names (the body is opaque text, resolved only at execute
time), so the column drop succeeded silently and every subsequent INSERT into the chunked
store threw `record "new" has no field "status"` — which the ingest dual-write swallows by
design. Result: 6 days of ingests wrote the parent but never chunked, and since retrieval
reads the chunked store, those notes were invisible to search. Neither the migration
preflight nor the smoke suite caught it.

Two invariants, each of which would have caught that incident:

  1. STALE TRIGGER — no trigger function on a checked table may reference a NEW./OLD.
     column that does not exist on the table the trigger fires on. (Catches the cause
     the moment a drop orphans a function body, before an insert ever fails.)
  2. CHUNK COVERAGE — every `current` row in public.knowledge must have >=1 child in
     public.knowledge_chunked. (Catches the effect: silently-unchunked ingests.)

Exit 0 if all invariants hold, 1 otherwise. Safe: read-only, never writes.

Usage:
    python scripts/chunk_integrity_check.py
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tables whose trigger functions we vet for stale column references.
_CHECKED_TABLES = ("knowledge", "knowledge_chunked")
# NEW./OLD. field references inside a PL/pgSQL body.
_REC_FIELD = re.compile(r"\b(?:NEW|OLD)\.([a-z_][a-z0-9_]*)", re.IGNORECASE)


def _load_env() -> None:
    env = ROOT / ".env.local"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def _columns(conn, table: str) -> set[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=%s",
        (table,),
    ).fetchall()
    return {r[0] for r in rows}


def check_stale_triggers(conn) -> list[str]:
    """Flag trigger functions referencing a NEW./OLD. column absent from their table."""
    problems: list[str] = []
    for table in _CHECKED_TABLES:
        cols = _columns(conn, table)
        if not cols:
            continue
        trigs = conn.execute(
            """SELECT t.tgname, p.proname, pg_get_functiondef(p.oid)
               FROM pg_trigger t
               JOIN pg_proc p ON p.oid = t.tgfoid
               WHERE t.tgrelid = ('public.' || %s)::regclass AND NOT t.tgisinternal""",
            (table,),
        ).fetchall()
        for tgname, proname, body in trigs:
            referenced = {m.lower() for m in _REC_FIELD.findall(body or "")}
            missing = sorted(r for r in referenced if r not in cols)
            if missing:
                problems.append(
                    f"STALE TRIGGER: {tgname} -> {proname}() on public.{table} "
                    f"references column(s) {missing} that do not exist on the table "
                    f"(every INSERT/UPDATE firing this trigger will error)."
                )
    return problems


def check_chunk_coverage(conn) -> list[str]:
    """Flag `current` knowledge rows with no children in knowledge_chunked."""
    n = conn.execute(
        """SELECT count(*) FROM public.knowledge k
           WHERE k.status = 'current'
             AND NOT EXISTS (SELECT 1 FROM public.knowledge_chunked c
                             WHERE c.document_id = k.id)"""
    ).fetchone()[0]
    if n:
        return [
            f"CHUNK COVERAGE: {n} 'current' knowledge row(s) have no chunk children. "
            f"Retrieval reads the chunked store, so these are invisible to search. "
            f"Re-chunk with: python scripts/backfill_orphan_chunks.py --execute"
        ]
    return []


def run_all(conn) -> list[str]:
    return check_stale_triggers(conn) + check_chunk_coverage(conn)


def main() -> int:
    _load_env()
    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print("no SUPABASE_DB_URL in env (.env.local)")
        return 2
    import psycopg

    conn = psycopg.connect(dsn)
    conn.autocommit = True
    problems = run_all(conn)
    if problems:
        print("chunk_integrity_check: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("chunk_integrity_check: OK (no stale triggers, full chunk coverage)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
