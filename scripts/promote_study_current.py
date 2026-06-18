#!/usr/bin/env python3
"""Promote non-superseded study/personal knowledge rows from 'historical' -> 'current'.

Rationale (Mike, 2026-06-17): `status` is a temporal-awareness signal — it exists so
stale OPERATIONAL state (e.g. an old homelab config) is not served as live. Study and
personal content has no supersession concept; each note is independent and evergreen.
The Stage-2 migration landed the entire corpus as 'historical', which (post-flip, with
the temporal-aware read path) would leave that content in the low-priority tier forever.

Scope (intentionally conservative):
  - Promote ONLY rows with `system IS NULL` (study/personal/reference) and
    `status = 'historical'` -> 'current'.
  - Rows with a non-null `system` (operational state: SpectreNet, PMX-01, OpenBrain,
    etc.) are LEFT historical — their "current" version is governed by the supersession
    path / wiki compilation, not a blanket promotion. Touching them could present stale
    operational state as live, the exact failure `status` is meant to prevent.

`system IS NULL` content is exempt from the duplicate-current trigger (001_knowledge_table.sql),
so promotion can never 409.

Dry-run by default; real writes only behind --execute (human-gated). Reversible via the
pre-cutover backup (~/ob_backup_*/knowledge.copy) or by setting the affected rows back to
'historical' (they are identifiable as the system-IS-NULL set).

Run:
  .venv/bin/python scripts/promote_study_current.py            # dry-run (default)
  .venv/bin/python scripts/promote_study_current.py --execute  # write (human-gated)
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def conninfo() -> str:
    url = ""
    for line in (ROOT / ".env.local").read_text().splitlines():
        if line.startswith("SUPABASE_DB_URL="):
            url = line.split("=", 1)[1].strip().strip('"').strip("'")
    p = urllib.parse.urlparse(url)
    return " ".join([
        f"host={p.hostname}", f"port={p.port or 5432}",
        f"dbname={(p.path or '/postgres').lstrip('/') or 'postgres'}",
        f"user={urllib.parse.unquote(p.username or '')}",
        f"password={urllib.parse.unquote(p.password or '')}", "sslmode=require"])


# Rows promoted: study/personal (system IS NULL), currently historical.
SELECT_CANDIDATES = """
    SELECT created_by, count(*) AS n
    FROM public.knowledge
    WHERE status = 'historical' AND system IS NULL
    GROUP BY created_by
    ORDER BY created_by
"""

PROMOTE_SQL = """
    UPDATE public.knowledge
    SET status = 'current'
    WHERE status = 'historical' AND system IS NULL
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="apply the promotion (default: dry-run, no writes)")
    args = ap.parse_args()

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(SELECT_CANDIDATES).fetchall()
        total = sum(r["n"] for r in rows)

        mode = "EXECUTE" if args.execute else "DRY RUN"
        print(f"[{mode}] promote study/personal historical -> current (system IS NULL)")
        print(f"  candidate rows: {total}")
        for r in rows:
            print(f"    {r['created_by']:18} {r['n']}")

        # Operational rows left untouched (reported for transparency).
        op = conn.execute(
            "SELECT count(*) AS n FROM public.knowledge "
            "WHERE status='historical' AND system IS NOT NULL"
        ).fetchone()["n"]
        print(f"  left historical (operational, system set): {op}")

        if not args.execute:
            print("\nDry run only. Re-run with --execute to apply "
                  "(after backup; safe to run before or after the 003 trigger).")
            return

        updated = conn.execute(PROMOTE_SQL).rowcount
        conn.commit()
        print(f"\n[EXECUTE] promoted {updated} rows historical -> current.")


if __name__ == "__main__":
    main()
