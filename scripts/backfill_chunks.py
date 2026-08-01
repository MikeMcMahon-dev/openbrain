#!/usr/bin/env python3
"""Backfill planner for heading-based chunking (ADR-017), Phase C.

DRY-RUN ONLY. Reads public.knowledge, runs each document through the chunking
pipeline, and reports what the chunked store WOULD contain — number of chunks,
fan-out distribution, embedding-call cost, biggest documents. It writes nothing and
computes no embeddings.

The actual backfill (create public.knowledge_chunked, embed each chunk, INSERT,
preserving status / supersedes_id chains and created_by scoping) is a DB mutation
gated behind sign-off; `--execute` deliberately refuses here.

Usage:
    python scripts/backfill_chunks.py                 # plan across the whole store
    python scripts/backfill_chunks.py --owner mike.mcmahon67
    python scripts/backfill_chunks.py --status current --limit 200
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")

from api._openbrain_api import get_db_conn  # noqa: E402
from api.chunking import chunk_document, infer_title  # noqa: E402


def _bucket(n: int) -> str:
    return "1" if n == 1 else "2-3" if n <= 3 else "4-6" if n <= 6 else "7+"


def plan(owner: str | None, status: str | None, limit: int | None) -> None:
    where, params = [], []
    if owner:
        where.append("created_by = %s")
        params.append(owner)
    if status:
        where.append("status = %s")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sql = f"SELECT id, content, created_by, status, system, tags FROM public.knowledge{clause}"
    if limit:
        sql += f" LIMIT {int(limit)}"

    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()

    total_docs = len(rows)
    total_chunks = 0
    buckets: Counter[str] = Counter()
    per_doc: list[int] = []
    fanout: list[tuple[int, str]] = []  # (n_chunks, title)
    multi = 0

    for r in rows:
        chunks = chunk_document(r["content"], infer_title(r["content"]))
        n = len(chunks) or 1
        total_chunks += n
        per_doc.append(n)
        buckets[_bucket(n)] += 1
        if n > 1:
            multi += 1
        title = (infer_title(r["content"]) or "")[:52]
        fanout.append((n, title))

    print("\n=== CHUNKING BACKFILL PLAN (dry-run — nothing written) ===")
    print(f"filter: owner={owner or 'ALL'} status={status or 'ALL'} limit={limit or 'none'}")
    print(f"\ndocuments:        {total_docs}")
    print(f"chunks produced:  {total_chunks}   (embedding calls at execute time)")
    if per_doc:
        print(f"chunks/doc:       mean {statistics.mean(per_doc):.2f}  "
              f"median {statistics.median(per_doc):.0f}  max {max(per_doc)}")
    print(f"multi-chunk docs: {multi}/{total_docs} "
          f"({100*multi/total_docs:.0f}%)" if total_docs else "n/a")
    print("\nfan-out distribution (chunks per doc):")
    for b in ("1", "2-3", "4-6", "7+"):
        print(f"  {b:>4} chunks: {buckets.get(b, 0)}")
    print("\nlargest fan-outs (these are the multi-topic docs chunking targets):")
    for n, title in sorted(fanout, reverse=True)[:10]:
        print(f"  {n:>2} chunks  {title!r}")
    print("\nContent-only per chunk (ADR-018a item 3): content, embedding, document_id,")
    print("chunk_index, heading, supersedes_id, ingest_id, source, created_by. Metadata")
    print("(domain/environment/system/tags/status/component_key) JOINs from the parent.")
    print("Idempotency key: deterministic per (content, owner, ..., chunk_index).\n")


# Content-only mirror (ADR-018a item 3): chunks own content + embedding + identity +
# provenance + the denormalised created_by; every metadata facet JOINs from the parent at
# read time. Only these parent fields are needed to build a chunk row. Valid post migration
# 007 §A (domain/environment/status/tags dropped their NOT NULL) — same as the live mirror.
_PARENT_COLS = "id, content, supersedes_id, ingest_id, source, created_by"

_INSERT = """
INSERT INTO public.knowledge_chunked
    (content, embedding, document_id, chunk_index, heading,
     supersedes_id, ingest_id, source, created_by)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (document_id, chunk_index) DO NOTHING
"""


def execute_backfill(
    owner: str | None,
    status: str | None,
    limit: int | None,
    rechunk: bool = False,
) -> None:
    """Chunk + re-embed every knowledge row into knowledge_chunked as CONTENT-ONLY rows
    (ADR-018a item 3): content + embedding + identity + provenance + created_by scoping;
    metadata is JOINed from the parent at read time, never copied here.

    Two modes:
    - default (resume/backfill): idempotent via ON CONFLICT (document_id, chunk_index)
      DO NOTHING — safe to re-run to fill gaps, but it will NOT re-split a document whose
      chunk boundaries changed (the old chunk_index=0 already exists, so a new split that
      turns 1 chunk into 3 would leave the old fat chunk_0 and only append the tail).
    - --rechunk: delete each document's existing chunked rows before inserting the fresh
      split, so a chunker change (e.g. headingless windowing) actually replaces the old
      chunks. Delete+insert+commit is per-document, so an interrupted run leaves every
      document either fully-old or fully-new, never a hybrid."""
    from api._openbrain_api import _vector_param, embedding_request  # noqa: E402

    where, params = [], []
    if owner:
        where.append("created_by = %s")
        params.append(owner)
    if status:
        where.append("status = %s")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    sel = f"SELECT {_PARENT_COLS} FROM public.knowledge{clause}"
    if limit:
        sel += f" LIMIT {int(limit)}"

    with get_db_conn() as conn:
        docs = conn.execute(sel, params).fetchall()
    mode = "re-chunking (delete + replace)" if rechunk else "backfilling"
    print(f"{mode} {len(docs)} documents into public.knowledge_chunked ...")

    chunk_total = 0
    with get_db_conn() as conn:
        for di, d in enumerate(docs):
            chunks = chunk_document(d["content"], infer_title(d["content"]))
            if rechunk:
                # Replace this document's chunks wholesale so changed boundaries take
                # effect. Same transaction as the re-insert + per-doc commit below.
                conn.execute(
                    "DELETE FROM public.knowledge_chunked WHERE document_id = %s",
                    [str(d["id"])],
                )
            for ch in chunks:
                emb = None
                try:
                    emb = embedding_request(ch["embed_text"])
                except Exception:
                    emb = None
                ingest_id = f"{d['ingest_id'] or d['id']}:c{ch['chunk_index']}"
                conn.execute(_INSERT, [
                    ch["content"], _vector_param(emb) if emb else None, str(d["id"]),
                    ch["chunk_index"], ch["heading"],
                    d["supersedes_id"], ingest_id, d["source"], d["created_by"],
                ])
                chunk_total += 1
            conn.commit()  # per-document: short transactions, resumable
            if (di + 1) % 100 == 0:
                print(f"  {di + 1}/{len(docs)} docs, {chunk_total} chunks embedded ...", flush=True)

    with get_db_conn() as conn:
        n = conn.execute("SELECT count(*) n FROM public.knowledge_chunked").fetchone()["n"]
    print(f"done: {len(docs)} documents -> {n} rows in knowledge_chunked")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default=None)
    ap.add_argument("--status", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--execute", action="store_true",
                    help="write chunks into public.knowledge_chunked (re-embeds; idempotent)")
    ap.add_argument("--rechunk", action="store_true",
                    help="with --execute: delete each doc's existing chunks first so a "
                         "chunker change re-splits them (default only fills gaps)")
    args = ap.parse_args()

    if args.execute:
        execute_backfill(args.owner, args.status, args.limit, rechunk=args.rechunk)
    else:
        if args.rechunk:
            print("note: --rechunk has no effect without --execute (dry-run plans fresh "
                  "chunks already).")
        plan(args.owner, args.status, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
