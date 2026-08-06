#!/usr/bin/env python3
"""Preflight for a schema-changing migration (ADR-018a process control; PM operational rubric).

Dumps the LIVE ground truth a migration's apply-order and blast-radius depend on — columns +
nullability + defaults, the REAL index/constraint names (so they are never guessed), the row
count — and every code path in the repo that reads or writes the table, so a migration is
validated from evidence instead of memory. This mechanises the manual pass whose absence nearly
shipped two defects (a fabricated index name and an un-migrated second writer) into an apply.

Read-only. No mutations. Run before authoring OR applying any ALTER/DROP:

    python scripts/preflight_migration.py knowledge_chunked

Exit 0 always on success — this is an evidence tool, not a gate; the PM reads its output against
the operational rubric and BLOCKs. Exit 2 on bad usage / unknown table.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_ENV = ROOT / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

import psycopg  # noqa: E402

_WRITE = re.compile(r"\b(INSERT\s+INTO|UPDATE|DELETE\s+FROM|ALTER\s+TABLE|DROP\s+TABLE)\b", re.I)
# NEW./OLD. field references inside a PL/pgSQL trigger function body.
_REC_FIELD = re.compile(r"\b(?:NEW|OLD)\.([a-z_][a-z0-9_]*)", re.I)


def _dsn() -> str | None:
    return os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")


def schema(table: str):
    """Live columns, real index + constraint names, and row count for `table`."""
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_nullable, column_default, data_type "
            "FROM information_schema.columns WHERE table_name = %s ORDER BY ordinal_position",
            [table],
        )
        cols = cur.fetchall()
        if not cols:
            return None
        cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s", [table])
        idx = cur.fetchall()
        cur.execute(
            "SELECT conname, contype, convalidated FROM pg_constraint "
            "WHERE conrelid = ('public.' || %s)::regclass ORDER BY conname",
            [table],
        )
        cons = cur.fetchall()
        cur.execute(
            "SELECT policyname, cmd, roles::text, qual, with_check "
            "FROM pg_policies WHERE tablename = %s ORDER BY policyname",
            [table],
        )
        pols = cur.fetchall()
        cur.execute("SELECT count(*) FROM public." + table)  # table name validated in main()
        n = cur.fetchone()[0]
    return cols, idx, cons, pols, n


def code_refs(table: str) -> list[tuple[str, str, str]]:
    """Every repo line mentioning the table, classified WRITE vs read/ref."""
    hits: list[tuple[str, str, str]] = []
    for base in ("api", "scripts", "supabase"):
        root = ROOT / base
        if not root.exists():
            continue
        for p in sorted(root.rglob("*.py")) + sorted(root.rglob("*.sql")):
            try:
                lines = p.read_text(errors="ignore").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if table in line and "preflight_migration" not in str(p):
                    kind = "WRITE" if _WRITE.search(line) else "read/ref"
                    hits.append((kind, f"{p.relative_to(ROOT)}:{i}", line.strip()[:90]))
    return hits


def triggers(table: str, table_cols: set[str]):
    """Triggers on the table + which of the table's columns each function body reads.

    THE blind spot behind the 2026-08-06 incident: Postgres records NO catalog dependency
    from a PL/pgSQL function body to the columns it names (the body is opaque text, resolved
    only at execute time). So DROP COLUMN succeeds silently even when a trigger function reads
    that column, and the error surfaces only at the next INSERT/UPDATE — which, for the ingest
    dual-write, was swallowed for 6 days. Enumerate them here so a column drop's real blast
    radius is on the record BEFORE the apply. Returns (tgname, proname, [cols_read]).
    """
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT t.tgname, p.proname, pg_get_functiondef(p.oid) "
            "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE t.tgrelid = ('public.' || %s)::regclass AND NOT t.tgisinternal "
            "ORDER BY t.tgname",
            [table],
        )
        rows = cur.fetchall()
    out = []
    for tgname, proname, body in rows:
        refs = {m.lower() for m in _REC_FIELD.findall(body or "")}
        out.append((tgname, proname, sorted(r for r in refs if r in table_cols)))
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: preflight_migration.py <table>")
        return 2
    table = sys.argv[1]
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
        print(f"refusing suspicious table name: {table!r}")
        return 2
    if not _dsn():
        print("no SUPABASE_DB_URL in env (.env.local)")
        return 2

    result = schema(table)
    if result is None:
        print(f"no such table: public.{table}")
        return 2
    cols, idx, cons, pols, n = result

    print(f"\n=== public.{table} — {n} rows ===")
    print("\ncolumns (name | nullable | default | type):")
    for name, nul, dflt, typ in cols:
        print(f"  {name:16} {nul:3}  {('' if dflt is None else str(dflt))[:26]:26} {typ}")
    print("\nindexes (REAL names — use these in DROP INDEX, never guess):")
    for name, d in idx:
        print(f"  {name}\n      {d[:112]}")
    print("\nconstraints (name | type | validated):")
    for name, ct, val in cons:
        print(f"  {name:38} {ct}  validated={val}")

    print("\nRLS policies (a policy referencing a dropped column BLOCKS the drop —")
    print("re-point it at the parent or drop it first):")
    if not pols:
        print("  (none)")
    for name, cmd, roles, qual, wc in pols:
        print(f"  {name}  ({cmd}, roles={roles})")
        if qual:
            print(f"      USING:      {qual}")
        if wc:
            print(f"      WITH CHECK: {wc}")

    col_names = {c[0] for c in cols}
    print("\ntriggers (a trigger FUNCTION body reading a column has NO catalog dependency —")
    print("Postgres DROPs the column silently and the function breaks at the next write):")
    trigs = triggers(table, col_names)
    if not trigs:
        print("  (none)")
    for tgname, proname, cols_read in trigs:
        print(f"  {tgname} -> {proname}()")
        if cols_read:
            print(f"      reads columns: {cols_read}")
            print(f"      ^ dropping any of these WITHOUT updating {proname}() = SILENT breakage")

    refs = code_refs(table)
    writers = [h for h in refs if h[0] == "WRITE"]
    readers = [h for h in refs if h[0] != "WRITE"]
    print(f"\nWRITERS ({len(writers)}) — EVERY one must be migrated before a")
    print("column it writes is dropped:")
    for _, loc, txt in writers:
        print(f"  {loc}:  {txt}")
    print(f"\nreaders / refs ({len(readers)}):")
    for _, loc, txt in readers:
        print(f"  {loc}:  {txt}")
    print("\nApply-order reminder (expand/contract): a reader/writer must STOP touching a")
    print("column before it is dropped; a NOT-NULL column needs DROP NOT NULL first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
