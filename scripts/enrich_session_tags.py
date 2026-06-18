#!/usr/bin/env python3
"""Enrich the tags on the six monitoring + OB2 session entries ingested 2026-06-18.

Context (ADR-013): those entries were ingested via the live MCP path, which DERIVES
tags from subject/topic and ignores producer-supplied `tags`. They landed with thin
tags (mostly just `shape:note`; the two interview entries also got Career/Interview
from the topic). Content retrieval is unaffected (text + vector), but tag faceting is.
This sets each row's descriptive tags to the intended, vocabulary-valid set while
preserving any namespaced (`shape:*` / `component:*`) tags already present.

Rows are matched by their exact `ingest_id` (stable; not content-dependent). Every
descriptive tag is validated against the live `public.tag_vocabulary` first, so the
003 validation trigger cannot reject the UPDATE at execute time.

Dry-run by default; real writes only behind --execute (human-gated). Non-destructive
and reversible (it only sets tags; revert by re-running with the prior values).

Run:
  .venv/bin/python scripts/enrich_session_tags.py            # dry-run (default)
  .venv/bin/python scripts/enrich_session_tags.py --execute  # write (human-gated)
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

# Distinctive content substring -> intended descriptive tags. (Matched by content
# rather than ingest_id: the MCP response returned the legacy compute_ingest_id hash,
# which differs from the knowledge.ingest_id column.) Namespaced tags like shape:* are
# preserved automatically from the existing row. All descriptive tags must exist in
# public.tag_vocabulary; validated before any write. Each substring must match exactly
# one source='live:text' row (the script aborts otherwise).
DESIRED: dict[str, list[str]] = {
    # Failure Detection Dashboard — overview / architecture / OCR cost
    "(multi-agent-lab, Week 3": ["MultiAgentLab", "Ops", "AI", "Architecture"],
    "monitoring architecture decisions": ["MultiAgentLab", "Ops", "Architecture", "AI"],
    "the OCR cost finding and model": ["MultiAgentLab", "AI", "Ops", "Architecture"],
    # OB2 cutover — lessons / two interview stories
    "lessons learned (June 2026)": ["OpenBrain", "Architecture", "Ops", "Reference"],
    "tell me about a time you dropped the ball": ["Career", "Interview", "OpenBrain"],
    "a hard bug / a production incident": ["Career", "Interview", "OpenBrain", "Ops"],
}


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true",
                    help="apply the tag updates (default: dry-run, no writes)")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY RUN"
    # prepare_threshold=None disables psycopg auto-prepare — the Supabase transaction
    # pooler (port 6543) rejects re-used prepared statements across the loop.
    with psycopg.connect(conninfo(), row_factory=dict_row, prepare_threshold=None) as conn:
        vocab = {r["tag"] for r in conn.execute("SELECT tag FROM public.tag_vocabulary").fetchall()}

        # Pre-validate every desired descriptive tag against the live vocabulary.
        unknown = sorted({t for tags in DESIRED.values() for t in tags if t not in vocab})
        if unknown:
            print(f"ABORT: these tags are not in public.tag_vocabulary: {unknown}")
            print("Add them via scripts/tag_review.py --approve first, or fix DESIRED.")
            sys.exit(1)

        print(f"[{mode}] enrich tags on {len(DESIRED)} session rows (matched by content)\n")
        plan = []
        for needle, desired in DESIRED.items():
            rows = conn.execute(
                "SELECT id, tags, left(content, 48) AS c FROM public.knowledge "
                "WHERE source = 'live:text' AND content LIKE %s",
                ["%" + needle + "%"],
            ).fetchall()
            if len(rows) != 1:
                print(f"ABORT: needle {needle!r} matched {len(rows)} rows (expected 1).")
                sys.exit(1)
            row = rows[0]
            current = list(row["tags"] or [])
            namespaced = [t for t in current if ":" in t]          # preserve shape:* etc.
            new_tags = desired + [t for t in namespaced if t not in desired]
            print(f"  {row['c']!r}")
            print(f"      {current}  ->  {new_tags}")
            plan.append((row["id"], new_tags))

        if not args.execute:
            print("\nDry run only. Re-run with --execute to apply (vocab pre-validated, "
                  "so the 003 trigger will accept these).")
            return

        updated = 0
        for rid, new_tags in plan:
            conn.execute("UPDATE public.knowledge SET tags = %s WHERE id = %s", [new_tags, rid])
            updated += 1
        conn.commit()
        print(f"\n[EXECUTE] updated tags on {updated} rows.")


if __name__ == "__main__":
    main()
