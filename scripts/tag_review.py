#!/usr/bin/env python3
"""Human approval console for the OpenBrain tag proposal queue (ADR-012).

The taxonomy is closed-by-default: ingest folds incoming tags to the controlled
vocabulary (public.tag_vocabulary, seeded from api/taxonomy_map.py CANONICAL_TAGS)
and any tag it does not recognise is NOT written to knowledge.tags — it is queued
in public.tag_proposals for a human. This CLI is that human's console:

  "The DB has these tags; use one, or make a new one."

Commands
    --list                  Show pending proposals + the current tag_vocabulary
                            for reference. Read-only; the default with no args.
    --approve TAG           Promote TAG into public.tag_vocabulary and mark its
                            proposal 'approved'. WRITE — needs --yes.
    --remap RAW=CANONICAL   Mark RAW's proposal 'remapped' to CANONICAL; optionally
                            rewrite existing knowledge.tags rows. Dry-run unless
                            --execute (the knowledge rewrite) / --yes (the proposal
                            status update).
    --reject TAG            Mark TAG's proposal 'rejected'. WRITE — needs --yes.

Safety
    * --list and all reads are always safe.
    * Mutations default to dry-run: --approve/--reject require --yes; --remap's
      knowledge rewrite requires --execute (and --yes to also flip the proposal).
    * Reads .env.local for SUPABASE_DB_URL at runtime — no hardcoded credentials.

Usage
    .venv/bin/python scripts/tag_review.py                      # = --list
    .venv/bin/python scripts/tag_review.py --approve Kustomize --yes
    .venv/bin/python scripts/tag_review.py --remap kube=K8s              # dry-run
    .venv/bin/python scripts/tag_review.py --remap kube=K8s --execute --yes
    .venv/bin/python scripts/tag_review.py --reject foobar --yes
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parent.parent


# ── DB plumbing (mirrors ingest/diag_retrieval.py) ─────────────────────────────


def db_url() -> str:
    env = ROOT / ".env.local"
    if not env.exists():
        raise SystemExit(f"{env} not found — cannot resolve SUPABASE_DB_URL")
    for line in env.read_text().splitlines():
        if line.startswith("SUPABASE_DB_URL="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("SUPABASE_DB_URL not found in .env.local")


def conninfo(url: str) -> str:
    p = urllib.parse.urlparse(url)
    user = urllib.parse.unquote(p.username or "")
    pw = urllib.parse.unquote(p.password or "")
    db = (p.path or "/postgres").lstrip("/") or "postgres"
    parts = [
        f"host={p.hostname}",
        f"port={p.port or 5432}",
        f"dbname={db}",
        f"user={user}",
        f"password={pw}",
        "sslmode=require",
    ]
    return " ".join(parts)


def connect():
    return psycopg.connect(conninfo(db_url()), row_factory=dict_row)


def _tag_key(tag: str | None) -> str:
    """Folded dedup key — must match api/taxonomy_map._tag_key exactly."""
    return (tag or "").strip().lower().replace(" ", "-").replace("_", "-")


# ── Commands ───────────────────────────────────────────────────────────────────


def cmd_list(conn) -> int:
    """Show pending proposals plus the current controlled vocabulary. Read-only."""
    print("=== current tag_vocabulary (the DB has these tags — reuse one) ===")
    try:
        vocab = [r["tag"] for r in conn.execute(
            "SELECT tag FROM public.tag_vocabulary ORDER BY tag"
        ).fetchall()]
    except Exception as exc:  # table missing / not applied yet
        print(f"  (tag_vocabulary unavailable: {exc})")
        vocab = []
    if vocab:
        for i in range(0, len(vocab), 6):
            print("  " + "  ".join(vocab[i:i + 6]))
    print(f"  -> {len(vocab)} canonical tags\n")

    print("=== pending tag_proposals (review queue) ===")
    try:
        rows = conn.execute(
            """
            SELECT normalized_key, raw_tag, occurrences, first_seen, last_seen,
                   sample_owner, sample_context
            FROM public.tag_proposals
            WHERE status = 'pending'
            ORDER BY occurrences DESC, last_seen DESC
            """
        ).fetchall()
    except Exception as exc:
        print(f"  (tag_proposals unavailable — migration 004 not applied? {exc})")
        return 0
    if not rows:
        print("  (no pending proposals)")
        return 0
    for r in rows:
        ctx = (r["sample_context"] or "").replace("\n", " ")
        if len(ctx) > 60:
            ctx = ctx[:57] + "..."
        print(
            f"  raw={r['raw_tag']!r:<28} x{r['occurrences']:<4} "
            f"owner={r['sample_owner'] or '-':<16} key={r['normalized_key']}"
        )
        if ctx:
            print(f"      sample: {ctx}")
    print(f"\n  -> {len(rows)} pending. Approve, remap, or reject each.")
    print("     approve: --approve TAG --yes")
    print("     remap:   --remap RAW=CANONICAL --execute --yes")
    print("     reject:  --reject TAG --yes")
    return 0


def _find_proposal(conn, raw: str):
    """Locate a proposal by raw tag or its normalized key. Returns row or None."""
    key = _tag_key(raw)
    return conn.execute(
        "SELECT * FROM public.tag_proposals WHERE normalized_key = %s",
        [key],
    ).fetchone()


def cmd_approve(conn, tag: str, yes: bool) -> int:
    """INSERT tag into tag_vocabulary and mark its proposal approved."""
    tag = tag.strip()
    if not tag:
        print("approve: empty tag")
        return 2
    prop = _find_proposal(conn, tag)
    if prop is None:
        print(f"approve: no proposal found for {tag!r} (key={_tag_key(tag)}). "
              "Approving will still add it to the vocabulary.")
    key = _tag_key(tag)
    print(f"APPROVE  vocab += {tag!r}; proposal[{key}].status = 'approved'")
    if not yes:
        print("  DRY-RUN. Re-run with --yes to write.")
        return 0
    with conn.transaction():
        conn.execute(
            "INSERT INTO public.tag_vocabulary (tag) VALUES (%s) "
            "ON CONFLICT (tag) DO NOTHING",
            [tag],
        )
        conn.execute(
            "UPDATE public.tag_proposals SET status = 'approved' WHERE normalized_key = %s",
            [key],
        )
    print(f"  done: {tag!r} is now canonical.")
    return 0


def cmd_remap(conn, spec: str, execute: bool, yes: bool) -> int:
    """Mark RAW remapped to CANONICAL; optionally rewrite knowledge.tags rows."""
    if "=" not in spec:
        print("remap: expected RAW=CANONICAL")
        return 2
    raw, canonical = (s.strip() for s in spec.split("=", 1))
    if not raw or not canonical:
        print("remap: both RAW and CANONICAL are required")
        return 2
    key = _tag_key(raw)

    # Sanity: target should already be canonical (in the vocabulary).
    try:
        in_vocab = conn.execute(
            "SELECT 1 FROM public.tag_vocabulary WHERE tag = %s", [canonical]
        ).fetchone()
    except Exception:
        in_vocab = None
    if not in_vocab:
        print(f"  WARNING: remap target {canonical!r} is not in tag_vocabulary. "
              "Approve it first (--approve) or the rewritten rows will be rejected "
              "by the knowledge tag-validation trigger.")

    # How many knowledge rows currently carry the raw tag? (read-only)
    affected = conn.execute(
        "SELECT count(*) AS c FROM public.knowledge WHERE %s = ANY(tags)", [raw]
    ).fetchone()["c"]

    print(f"REMAP  {raw!r} -> {canonical!r}  (key={key})")
    print(f"  knowledge rows carrying {raw!r}: {affected}")
    print(f"  proposal[{key}].status = 'remapped', remap_to = {canonical!r}")

    if not execute:
        print(f"\n  DRY-RUN (no --execute): would rewrite {affected} knowledge row(s).")
        print("  Re-run with --execute to rewrite knowledge.tags, and --yes to also "
              "flip the proposal status.")
        return 0

    with conn.transaction():
        # array_replace swaps the raw tag for the canonical one in place.
        conn.execute(
            "UPDATE public.knowledge SET tags = array_replace(tags, %s, %s) "
            "WHERE %s = ANY(tags)",
            [raw, canonical, raw],
        )
        if yes:
            conn.execute(
                "UPDATE public.tag_proposals "
                "SET status = 'remapped', remap_to = %s WHERE normalized_key = %s",
                [canonical, key],
            )
    print(f"  done: rewrote {affected} knowledge row(s) {raw!r} -> {canonical!r}.")
    if not yes:
        print("  NOTE: proposal status NOT updated (no --yes). Knowledge rows were "
              "rewritten; re-run with --yes to mark the proposal remapped.")
    return 0


def cmd_reject(conn, tag: str, yes: bool) -> int:
    """Mark a proposal rejected (retire it; do not add to vocabulary)."""
    tag = tag.strip()
    if not tag:
        print("reject: empty tag")
        return 2
    key = _tag_key(tag)
    prop = _find_proposal(conn, tag)
    if prop is None:
        print(f"reject: no proposal found for {tag!r} (key={key}); nothing to do.")
        return 0
    print(f"REJECT  proposal[{key}].status = 'rejected'  (raw={prop['raw_tag']!r})")
    if not yes:
        print("  DRY-RUN. Re-run with --yes to write.")
        return 0
    with conn.transaction():
        conn.execute(
            "UPDATE public.tag_proposals SET status = 'rejected' WHERE normalized_key = %s",
            [key],
        )
    print(f"  done: {tag!r} rejected.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Tag proposal review console (ADR-012). Default = --list.",
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--list", action="store_true", help="list pending proposals (default)")
    g.add_argument("--approve", metavar="TAG", help="add TAG to tag_vocabulary")
    g.add_argument("--remap", metavar="RAW=CANONICAL", help="remap RAW onto CANONICAL")
    g.add_argument("--reject", metavar="TAG", help="reject TAG's proposal")
    ap.add_argument("--yes", action="store_true",
                    help="confirm a mutation (approve/reject/remap status update)")
    ap.add_argument("--execute", action="store_true",
                    help="for --remap: actually rewrite knowledge.tags (else dry-run)")
    args = ap.parse_args(argv)

    with connect() as conn:
        if args.approve:
            return cmd_approve(conn, args.approve, args.yes)
        if args.remap:
            return cmd_remap(conn, args.remap, args.execute, args.yes)
        if args.reject:
            return cmd_reject(conn, args.reject, args.yes)
        # default and --list
        return cmd_list(conn)


if __name__ == "__main__":
    sys.exit(main())
