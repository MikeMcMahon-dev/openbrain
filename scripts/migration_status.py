#!/usr/bin/env python3
"""migration_status.py — what is actually applied to production, read from the live catalog.

`supabase/migrations/migration_log.md` is the human-readable record; this is the check that keeps
it honest. Every migration is verified by an object that must exist (or must be gone), never by
trusting a file header — on 2026-08-23 both 007 and 012 still read "STAGED — NOT YET APPLIED"
while their objects were live in production.

    python scripts/migration_status.py          # table of APPLIED / MISSING
    python scripts/migration_status.py --quiet   # exit 1 if anything is MISSING, no output

Adding a migration? Add its check here AND a row in migration_log.md, in the same change.
"""
from __future__ import annotations

import os
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

# (label, SQL returning a single boolean: True when the migration IS applied)
CHECKS: list[tuple[str, str]] = [
    ("001 knowledge",             "SELECT to_regclass('public.knowledge') IS NOT NULL"),
    ("002 wiki_pages",            "SELECT to_regclass('public.wiki_pages') IS NOT NULL"),
    ("003 tag_vocabulary",        "SELECT to_regclass('public.tag_vocabulary') IS NOT NULL"),
    ("004 tag_proposals",         "SELECT to_regclass('public.tag_proposals') IS NOT NULL"),
    ("005 knowledge_chunked",     "SELECT to_regclass('public.knowledge_chunked') IS NOT NULL"),
    ("006 component_key+system",
     """SELECT count(*) = 2 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='knowledge'
           AND column_name IN ('component_key','system')"""),
    ("007 chunks content-only",
     """SELECT count(*) = 0 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='knowledge_chunked'
           AND column_name IN ('status','system','tags','domain','environment','component_key')"""),
    ("008 supersession_events",
     """SELECT EXISTS (SELECT 1 FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
          WHERE c.relname='supersession_events' AND t.tgname='supersession_events_no_mutate')"""),
    ("009 contradiction_candidates",
     "SELECT to_regclass('public.contradiction_candidates') IS NOT NULL"),
    ("010 bitemporal",
     """SELECT count(*) = 2 FROM information_schema.columns
         WHERE table_schema='public' AND table_name='knowledge'
           AND column_name IN ('valid_from','valid_until')"""),
    ("011 stale trigger dropped",
     "SELECT count(*) = 0 FROM pg_proc WHERE proname='validate_knowledge_chunked_insert'"),
    ("012 retirement_requests",   "SELECT to_regclass('public.retirement_requests') IS NOT NULL"),
    ("013 retirement FK dropped",
     """SELECT count(*) = 0 FROM pg_constraint
         WHERE conrelid='public.retirement_requests'::regclass AND contype='f'"""),
]


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv
    missing = 0
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None) as conn:
        for label, sql in CHECKS:
            try:
                applied = bool(conn.execute(sql).fetchone()[0])
            except Exception as exc:
                # A check whose own table is absent must read MISSING, not crash the report.
                conn.rollback()
                applied = False
                if not quiet:
                    print(f"  {label:<30} ERROR   {str(exc).splitlines()[0][:60]}")
                missing += 1
                continue
            if not applied:
                missing += 1
            if not quiet:
                print(f"  {label:<30} {'APPLIED' if applied else 'MISSING'}")
    if not quiet:
        print(f"\n{len(CHECKS) - missing}/{len(CHECKS)} applied."
              + ("" if not missing else f"  {missing} MISSING — see migration_log.md"))
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
