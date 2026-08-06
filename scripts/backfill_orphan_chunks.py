#!/usr/bin/env python3
"""Backfill chunks for `knowledge` rows that have no children in `knowledge_chunked`.

WHY THIS EXISTS: from 2026-08-01 (migration 007's column drop) until the stale-trigger
fix (migration 011), every INSERT into public.knowledge_chunked raised
`record "new" has no field "status"` from the obsolete validate_knowledge_chunked_insert()
trigger. The ingest dual-write swallows that error by design (a chunk failure must never
fail the canonical parent write), so parents committed but chunks silently never landed —
and since retrieval reads the chunked store, those notes became invisible to search.
This tool re-chunks the orphaned parents through the SAME code path a live ingest uses
(`_dual_write_chunks`), so the result is byte-identical to what should have been written.

Idempotent: `_dual_write_chunks` inserts ON CONFLICT (document_id, chunk_index) DO NOTHING,
so re-running is safe. Dry-run by default; pass --execute to write.

Usage:
    python scripts/backfill_orphan_chunks.py                 # plan only (no writes)
    python scripts/backfill_orphan_chunks.py --status current
    python scripts/backfill_orphan_chunks.py --execute       # do the backfill
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.local")

import psycopg  # noqa: E402

from api.chunking import chunk_document, infer_title  # noqa: E402
from api.knowledge_ingest import _dual_write_chunks  # noqa: E402


def _orphans(conn, status: str | None) -> list[dict]:
    where = "NOT EXISTS (SELECT 1 FROM public.knowledge_chunked c WHERE c.document_id = k.id)"
    params: list = []
    if status:
        where += " AND k.status = %s"
        params.append(status)
    rows = conn.execute(
        f"""SELECT k.id::text, k.content, k.created_by, k.source, k.supersedes_id::text,
                   k.ingest_id, k.status, k.created_at::timestamp(0) AS created_at
            FROM public.knowledge k
            WHERE {where}
            ORDER BY k.created_at""",
        params,
    ).fetchall()
    cols = ["id", "content", "created_by", "source", "supersedes_id",
            "ingest_id", "status", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", default="current",
                    help="only backfill this parent status (default: current; '' = all)")
    ap.add_argument("--execute", action="store_true",
                    help="actually write chunks (default is a dry-run plan)")
    args = ap.parse_args()
    status = args.status or None

    import os
    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    conn = psycopg.connect(dsn)
    conn.autocommit = True

    orphans = _orphans(conn, status)
    if not orphans:
        print(f"No orphan rows (status={status or 'ANY'}). Nothing to do.")
        return 0

    print(f"{len(orphans)} orphan row(s) (status={status or 'ANY'}):")
    total_chunks = 0
    for o in orphans:
        planned = chunk_document(o["content"], infer_title(o["content"]))
        total_chunks += len(planned)
        print(f"  {o['id']}  {o['created_at']}  status={o['status']:9} "
              f"words={len(o['content'].split()):4}  -> {len(planned)} chunk(s)")
    print(f"Total chunks to write: {total_chunks}")

    if not args.execute:
        print("\nDRY RUN — nothing written. Re-run with --execute to backfill.")
        return 0

    print("\nExecuting backfill (idempotent, ON CONFLICT DO NOTHING)...")
    written = 0
    for o in orphans:
        _dual_write_chunks(
            o["id"], o["content"], o["created_by"],
            source=o["source"] or "backfill:orphan-chunks",
            supersedes_id=o["supersedes_id"],
            ingest_id=o["ingest_id"],
        )
        n = conn.execute(
            "SELECT count(*) FROM public.knowledge_chunked WHERE document_id = %s",
            (o["id"],),
        ).fetchone()[0]
        written += n
        print(f"  {o['id']} -> {n} chunk(s) now present")
    print(f"Done. {written} chunk row(s) across {len(orphans)} document(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
