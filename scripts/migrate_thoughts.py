#!/usr/bin/env python3
"""
OpenBrain 2.0: migrate public.thoughts -> public.knowledge

Default: dry-run (prints plan, writes nothing).
Pass --execute to write to the database. Requires human approval first.

All migrated records receive status='historical'. No auto-promotion to current.
Run verify step confirms COUNT(knowledge) == COUNT(thoughts) before closing Stage 2.

Usage:
  python3 scripts/migrate_thoughts.py           # dry-run
  python3 scripts/migrate_thoughts.py --execute # write to DB (human approval required)
"""

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg
import yaml

REPO_ROOT = Path(__file__).parent.parent
DISCOVERY_DOC = REPO_ROOT / "docs" / "migrations" / "001_domain_discovery.md"
REPORT_PATH = REPO_ROOT / "docs" / "migrations" / "001_migration_report.md"
MIGRATION_SOURCE = f"migration:thoughts:{datetime.now(timezone.utc).date()}"


# ── Mapping loader ─────────────────────────────────────────────────────────────

def load_domain_mapping() -> dict:
    """Parse the ```yaml mapping block from 001_domain_discovery.md."""
    if not DISCOVERY_DOC.exists():
        raise FileNotFoundError(
            f"Domain discovery doc not found: {DISCOVERY_DOC}\n"
            "Stage 1 must complete before Stage 2."
        )
    text = DISCOVERY_DOC.read_text()
    # Extract the yaml block that starts with "mapping:"
    match = re.search(r"```yaml\s*(mapping:.*?)```", text, re.DOTALL)
    if not match:
        raise ValueError(f"No ```yaml mapping: block found in {DISCOVERY_DOC}")
    return yaml.safe_load(match.group(1))["mapping"]


# ── Row classifier ─────────────────────────────────────────────────────────────

def classify_row(
    subject: str | None,
    topic: str | None,
    source_uri: str | None,
    source_type: str | None,
    mapping: dict,
) -> dict:
    """
    Return {domain, environment, system, tags} for a thoughts row.

    For entries with _path_rules (e.g. engineering/notes), source_uri
    and source_type are used for sub-classification. First-match wins.
    Falls back to __malformed__ (subject > 80 chars) or __default__ (null/unknown).
    """
    # Null subject → default
    if subject is None:
        return _flatten(mapping.get("__default__", _study_default()))

    # Malformed subject (full sentence used as subject key)
    if len(subject) > 80:
        return _flatten(mapping.get("__malformed__", _study_default()))

    entry = mapping.get(subject)
    if entry is None:
        return _flatten(mapping.get("__default__", _study_default()))

    # Path-based classification
    if "_path_rules" in entry:
        # Text source with no file path → operational state note
        if source_type == "text" and not source_uri:
            rule = entry.get("_text_source_rule", entry.get("_default", _study_default()))
            return _flatten(rule)
        # Match vault path prefix in order (first match wins)
        if source_uri:
            for rule in entry["_path_rules"]:
                if rule["prefix"] in source_uri:
                    return _flatten(rule)
        return _flatten(entry.get("_default", _study_default()))

    return _flatten(entry)


def _flatten(entry: dict) -> dict:
    return {
        "domain": entry.get("domain", "Study"),
        "environment": entry.get("environment", "Study"),
        "system": entry.get("system"),
        "tags": entry.get("tags", []),
    }


def _study_default() -> dict:
    return {"domain": "Study", "environment": "Study", "system": None, "tags": []}


# ── DB connection ──────────────────────────────────────────────────────────────

def get_db_url() -> str:
    env_file = REPO_ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            m = re.match(r"^SUPABASE_DB_URL=(.*)", line.strip())
            if m:
                return m.group(1).strip("\"'")
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError("SUPABASE_DB_URL not found in .env.local or environment")
    return url


# ── Migration ─────────────────────────────────────────────────────────────────

def migrate(dry_run: bool = True) -> None:
    mapping = load_domain_mapping()
    url = get_db_url()

    conn = psycopg.connect(url)
    cur = conn.cursor()

    # Read all thoughts rows
    cur.execute("""
        SELECT
            id,
            content,
            embedding,
            metadata,
            created_at,
            source_type,
            source_uri,
            source_chunk_id,
            content_hash,
            created_by_user_login
        FROM public.thoughts
        ORDER BY created_at
    """)
    rows = cur.fetchall()
    total = len(rows)

    print(f"{'[DRY RUN] ' if dry_run else ''}Migrating {total} rows from thoughts → knowledge")
    print(f"source tag: {MIGRATION_SOURCE}")
    print()

    domain_counts: dict[str, int] = {}
    env_counts: dict[str, int] = {}
    path_fallbacks = 0
    malformed_subjects = 0
    null_subjects = 0
    prepared = []

    for row in rows:
        (
            row_id, content, embedding, metadata, created_at,
            source_type, source_uri, source_chunk_id, content_hash,
            created_by_user_login,
        ) = row

        meta = metadata or {}
        subject = meta.get("subject")
        topic = meta.get("topic")
        owner = meta.get("owner") or created_by_user_login or "mmcmahon"

        # ingest_id: prefer source_chunk_id, fall back to content_hash
        ingest_id = source_chunk_id or content_hash

        classification = classify_row(subject, topic, source_uri, source_type, mapping)

        # Audit counters
        if subject is None:
            null_subjects += 1
        elif len(subject) > 80:
            malformed_subjects += 1
        if classification["domain"] == "Study" and classification["environment"] == "Study" \
                and subject and subject == "engineering" and source_uri \
                and not any(r["prefix"] in source_uri for r in mapping.get("engineering", {}).get("_path_rules", [])):
            path_fallbacks += 1

        domain_counts[classification["domain"]] = domain_counts.get(classification["domain"], 0) + 1
        env_counts[classification["environment"]] = env_counts.get(classification["environment"], 0) + 1

        prepared.append({
            "content": content,
            "embedding": embedding,
            "domain": classification["domain"],
            "environment": classification["environment"],
            "system": classification["system"],
            "tags": classification["tags"],
            "status": "historical",
            "ingest_id": ingest_id,
            "source": MIGRATION_SOURCE,
            "created_by": owner,
            "created_at": created_at,
        })

    # Print classification summary
    print("Domain distribution:")
    for d, c in sorted(domain_counts.items(), key=lambda x: -x[1]):
        print(f"  {d}: {c}")
    print("\nEnvironment distribution:")
    for e, c in sorted(env_counts.items(), key=lambda x: -x[1]):
        print(f"  {e}: {c}")
    print(f"\nNull subjects (→ __default__): {null_subjects}")
    print(f"Malformed subjects (→ __malformed__): {malformed_subjects}")
    print(f"Path fallbacks in engineering bucket: {path_fallbacks}")

    if dry_run:
        print(f"\n[DRY RUN] Would insert {len(prepared)} rows into public.knowledge")
        print("[DRY RUN] No changes made. Pass --execute to write.")
        _write_report(total, prepared, domain_counts, env_counts, dry_run=True)
        conn.close()
        return

    # Write to knowledge table
    print(f"\nInserting {len(prepared)} rows into public.knowledge …")
    inserted = 0
    for p in prepared:
        cur.execute("""
            INSERT INTO public.knowledge
              (content, embedding, domain, environment, system, tags,
               status, ingest_id, source, created_by, created_at)
            VALUES
              (%(content)s, %(embedding)s, %(domain)s, %(environment)s, %(system)s, %(tags)s,
               %(status)s, %(ingest_id)s, %(source)s, %(created_by)s, %(created_at)s)
        """, p)
        inserted += 1
        if inserted % 100 == 0:
            print(f"  … {inserted}/{total}")

    conn.commit()

    # Verification
    cur.execute("SELECT COUNT(*) FROM public.knowledge")
    knowledge_count = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM public.thoughts")
    thoughts_count = cur.fetchone()[0]

    print("\nVerification:")
    print(f"  thoughts rows: {thoughts_count}")
    print(f"  knowledge rows: {knowledge_count}")

    if knowledge_count != thoughts_count:
        print(f"  !! COUNT MISMATCH — expected {thoughts_count}, got {knowledge_count}")
        conn.close()
        sys.exit(1)
    else:
        print("  ✓ Counts match")

    _write_report(total, prepared, domain_counts, env_counts, dry_run=False)
    conn.close()
    print(f"\nMigration complete. Report: {REPORT_PATH}")


def _write_report(
    total: int,
    prepared: list,
    domain_counts: dict,
    env_counts: dict,
    dry_run: bool,
) -> None:
    lines = [
        "# OB2 Migration Report",
        "",
        f"**Date:** {datetime.now(timezone.utc).date()}",
        f"**Mode:** {'DRY RUN' if dry_run else 'EXECUTED'}",
        f"**Source:** `{MIGRATION_SOURCE}`",
        f"**Rows processed:** {total}",
        "",
        "## Domain distribution",
        "",
    ]
    for d, c in sorted(domain_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {d}: {c}")
    lines += ["", "## Environment distribution", ""]
    for e, c in sorted(env_counts.items(), key=lambda x: -x[1]):
        lines.append(f"- {e}: {c}")
    lines += [
        "",
        "## Notes",
        "",
        "- All rows migrated with `status='historical'`",
        "- No rows auto-promoted to `current`",
        "- Human review required before promoting any row to `current`",
        f"- Report generated: {datetime.now(timezone.utc).isoformat()}",
    ]
    REPORT_PATH.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate public.thoughts → public.knowledge")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write to DB. Default is dry-run only. Requires human approval.",
    )
    args = parser.parse_args()

    if args.execute:
        confirm = input(
            "\n!! EXECUTE mode: this will write to production Supabase.\n"
            "Type 'yes' to confirm: "
        )
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    migrate(dry_run=not args.execute)
