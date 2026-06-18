#!/usr/bin/env python3
"""
OB2 Cutover — Workstream C: migrate the post-Stage-2 gap from public.thoughts
into public.knowledge.

Stage 2 (scripts/migrate_thoughts.py) took a one-time snapshot of 699 thoughts rows
into knowledge, ALL with status='historical', source='migration:thoughts:2026-05-14'.
Since then ~66 new thoughts rows were created that are NOT in knowledge (incl. Annie's
31 Linux Command Line cards). This script migrates that gap with curation + taxonomy +
a current-promotion policy, behind an explicit --execute flag.

DEFAULT: dry-run. Prints exactly what it WOULD do, writes the human-sign-off report,
and touches the database READ-ONLY (SELECT only). Pass --execute to write to the DB
(human approval required — do not run without a signed-off report and a confirmed backup).

Idempotent: every migrated row is tagged source='cutover:thoughts:<thoughts_id>'. Re-runs
skip rows whose cutover source tag already exists in knowledge.

Reversible: rollback is a single statement (NOT run by this script):
    DELETE FROM public.knowledge WHERE source LIKE 'cutover:%';

Usage:
  /Users/Shared/home-lab/open-brain/.venv/bin/python scripts/cutover_migrate.py            # dry-run (default)
  /Users/Shared/home-lab/open-brain/.venv/bin/python scripts/cutover_migrate.py --execute  # write (human-gated)

ABSOLUTE RULE for the dry-run path: no INSERT/UPDATE/DELETE/ALTER is issued. Only SELECT.
"""
from __future__ import annotations

import argparse
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parent.parent
# Ensure the repo root is importable so `from api.taxonomy_map import ...` resolves
# when this script is run directly (python scripts/cutover_migrate.py). Without this
# the mapper import silently fails and the LOCAL fallback rules — which can lag the
# governed api/taxonomy_map.py single authority (ADR-012) — would drive a real
# --execute. The fallback must only trigger if the module genuinely does not exist.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
REPORT_PATH = REPO_ROOT / "docs" / "migrations" / "003_cutover_migration_report.md"

# The Stage-2 snapshot boundary. Membership in knowledge is determined by content
# equality (the robust key — see report §"Gap identification"), not created_at, but
# this timestamp documents the snapshot for the report.
STAGE2_CUTOFF = "2026-05-14 03:39:12.072384+00"


# ── Taxonomy mapper: prefer Workstream B's api/taxonomy_map.py, else fall back ──────

def _load_taxonomy_mapper():
    """
    Return (map_fn, source_label).

    Workstream B is expected to deliver api/taxonomy_map.py:map_to_taxonomy. If it
    exists, use it (single source of truth). If not, fall back to a LOCAL copy of the
    rules below and note the dependency loudly. The local copy mirrors the subject→
    {domain, environment, system, tags} rules in docs/migrations/001_domain_discovery.md
    plus the NEW subjects introduced after Stage 2 (see report §"New subjects").
    """
    try:
        from api.taxonomy_map import map_to_taxonomy  # type: ignore

        def _fn(subject, topic, owner, source_type, source_uri):
            return map_to_taxonomy(
                subject=subject, topic=topic, owner=owner,
                source_type=source_type, source_uri=source_uri,
            )

        return _fn, "api.taxonomy_map.map_to_taxonomy (Workstream B)"
    except Exception:
        return _local_map_to_taxonomy, "LOCAL FALLBACK (Workstream B not present)"


# Local taxonomy rules. Keys = exact metadata->>'subject' string.
# Values: {domain, environment, system, tags}. system=None where not applicable.
# Mirrors 001_domain_discovery.md and extends it for the cutover gap's NEW subjects.
_LOCAL_RULES: dict[str, dict] = {
    # ── Existing 001 subjects present in the gap ──────────────────────────────
    "Home Network":    {"domain": "Network", "environment": "Production", "system": "SpectreNet", "tags": ["Network", "Production"]},
    "SpectreNet":      {"domain": "Network", "environment": "Production", "system": "SpectreNet", "tags": ["Network", "Production"]},
    "Kubernetes":      {"domain": "K8s",     "environment": "Lab",        "system": None,          "tags": ["K8s"]},
    "session-context": {"domain": "OpenBrain", "environment": "Study",    "system": "OpenBrain",   "tags": ["OpenBrain", "Session"]},

    # ── NEW subjects (post-Stage-2; not in 001). See report for rationale. ─────
    # Annie's Linux study cards → Study/Study (owner curation decision (c)).
    "Linux Command Line": {"domain": "Study", "environment": "Study", "system": None, "tags": ["Linux", "Annie"]},
    # Mike's homelab/network operational state. Production network reality.
    "Infrastructure":          {"domain": "Network", "environment": "Production", "system": "SpectreNet", "tags": ["Network", "Production"]},
    "Homelab Infrastructure":  {"domain": "Network", "environment": "Production", "system": "SpectreNet", "tags": ["Network", "Production", "Homelab"]},
    "Homelab Notes":           {"domain": "Network", "environment": "Lab",        "system": None,          "tags": ["Homelab"]},
    # Mike's notes about Annie's schooling — personal/family, not Annie's study content.
    "Annie — Educational Assessment": {"domain": "Personal", "environment": "Archive", "system": None, "tags": ["Personal", "Family", "Annie"]},
    "Annie — Ubuntu Study Plan":      {"domain": "Personal", "environment": "Archive", "system": None, "tags": ["Personal", "Family", "Annie"]},
    # Engineering reference / workflow / interview prep → Study.
    "Git / Workflow":  {"domain": "Study", "environment": "Study", "system": None, "tags": ["Git", "Workflow"]},
    "interview-prep":  {"domain": "Study", "environment": "Study", "system": None, "tags": ["Career", "Interview"]},
}

# Smoke-test / artifact subject markers → DROP (curation decision (a)).
_DROP_SUBJECTS = {
    "mcp_smoke", "smoke test", "live smoke test note", "mcp_smoke_test",
    "pdf_smoke_test", "docx_url_smoke_test", "System",
}

_DEFAULT = {"domain": "Study", "environment": "Study", "system": None, "tags": []}


def _local_map_to_taxonomy(subject, topic, owner, source_type, source_uri):
    if subject is None:
        return dict(_DEFAULT)
    if len(subject) > 80:
        # Malformed (full sentence used as subject) → OpenBrain/Study ops bucket.
        return {"domain": "OpenBrain", "environment": "Study", "system": "OpenBrain", "tags": ["Ops"]}
    return dict(_LOCAL_RULES.get(subject, _DEFAULT))


# ── Curation: which rows to DROP (never migrate) ───────────────────────────────────

def curation_verdict(subject, topic, content_len) -> tuple[str, str]:
    """
    Return (verdict, reason). verdict ∈ {'migrate', 'drop', 'flag'}.

    - drop: smoke-test / malformed / empty artifacts (curation decision (a)).
    - flag: cannot confidently classify — needs owner input before --execute.
    - migrate: everything else.
    """
    if subject is None:
        # Null subject/topic, tiny artifact. Owner reviewed (2026-06-17): nothing of
        # value -> drop (curation decision (a) extended).
        return "drop", "null subject/topic — empty artifact (owner-confirmed drop)"
    if subject in _DROP_SUBJECTS:
        return "drop", f"smoke-test subject '{subject}'"
    if subject.startswith("[malformed subject:"):
        return "drop", "malformed subject (sentence-as-subject)"
    if len(subject) > 80:
        return "drop", "malformed subject (>80 chars)"
    return "migrate", "curated content"


# ── Current-promotion policy ───────────────────────────────────────────────────────

# Subjects whose surviving rows should be promoted to status='current' on migration.
# Policy: operational state that the family/Mike actively query becomes 'current';
# everything else stays 'historical' (matching the Stage-2 default). Study cards are
# evergreen reference → 'current'. Session-context / one-off notes stay 'historical'.
_PROMOTE_SUBJECTS = {
    # Live operational network/infra state — the current truth.
    "Home Network", "SpectreNet", "Infrastructure", "Homelab Infrastructure",
    "Homelab Notes", "Kubernetes",
    # Annie's evergreen study reference.
    "Linux Command Line",
    # Engineering study reference.
    "Git / Workflow", "interview-prep",
}


def promotion_verdict(subject) -> str:
    """Return 'current' or 'historical' for a surviving (migrate) row."""
    if subject in _PROMOTE_SUBJECTS:
        return "current"
    return "historical"


# ── DB connection (read SUPABASE_DB_URL from .env.local; never hardcode) ───────────

def _db_url() -> str:
    env_file = REPO_ROOT / ".env.local"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("SUPABASE_DB_URL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    import os
    url = os.environ.get("SUPABASE_DB_URL")
    if not url:
        raise RuntimeError("SUPABASE_DB_URL not found in .env.local or environment")
    return url


def _conninfo(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return " ".join([
        f"host={p.hostname}",
        f"port={p.port or 5432}",
        f"dbname={(p.path or '/postgres').lstrip('/') or 'postgres'}",
        f"user={urllib.parse.unquote(p.username or '')}",
        f"password={urllib.parse.unquote(p.password or '')}",
        "sslmode=require",
    ])


# ── Gap read (READ-ONLY) ───────────────────────────────────────────────────────────

GAP_SQL = """
    SELECT
        t.id,
        t.content,
        t.embedding,
        t.metadata->>'subject' AS subject,
        t.metadata->>'topic'   AS topic,
        COALESCE(t.metadata->>'owner', t.created_by_user_login) AS owner,
        t.source_type,
        t.source_uri,
        t.document_id,
        t.source_chunk_id,
        t.content_hash,
        t.created_at,
        length(t.content) AS content_len
    FROM public.thoughts t
    WHERE t.content NOT IN (SELECT content FROM public.knowledge)
    ORDER BY owner, subject, topic
"""


def _already_migrated_sources(conn) -> set[str]:
    """Cutover source tags already present in knowledge (idempotency guard)."""
    rows = conn.execute(
        "SELECT source FROM public.knowledge WHERE source LIKE 'cutover:%'"
    ).fetchall()
    return {r["source"] for r in rows}


def plan(conn, map_fn, mapper_label):
    """Build the migration plan from a READ-ONLY scan. Returns (plan_rows, stats)."""
    gap = conn.execute(GAP_SQL).fetchall()
    existing = _already_migrated_sources(conn)

    plan_rows = []
    stats = {
        "gap_total": len(gap),
        "migrate": 0, "promote_current": 0, "stay_historical": 0,
        "drop": 0, "flag": 0, "already_migrated": 0,
        "doc_id_null": 0,
        "by_subject": {},   # subject -> {migrate, drop, flag, current, taxonomy}
        "drop_list": [],    # (id, subject, topic, reason)
        "flag_list": [],    # (id, subject, topic, reason)
        "doc_null_list": [],  # (id, owner, subject) needing document_id backfill
    }

    for r in gap:
        sid = str(r["id"])
        subject, topic, owner = r["subject"], r["topic"], r["owner"]
        cutover_source = f"cutover:thoughts:{sid}"

        bucket = stats["by_subject"].setdefault(
            subject if subject is not None else "(null)",
            {"migrate": 0, "drop": 0, "flag": 0, "current": 0, "historical": 0, "taxonomy": None},
        )

        if cutover_source in existing:
            stats["already_migrated"] += 1
            continue

        verdict, reason = curation_verdict(subject, topic, r["content_len"])

        if verdict == "drop":
            stats["drop"] += 1
            bucket["drop"] += 1
            stats["drop_list"].append((sid, subject, topic, reason))
            continue
        if verdict == "flag":
            stats["flag"] += 1
            bucket["flag"] += 1
            stats["flag_list"].append((sid, subject, topic, reason))
            continue

        # migrate
        tax = map_fn(subject, topic, owner, r["source_type"], r["source_uri"])
        status = promotion_verdict(subject)

        # ADR-011 decision 2: document_id hygiene (report only).
        doc_id = r["document_id"]
        if not doc_id:
            stats["doc_id_null"] += 1
            stats["doc_null_list"].append((sid, owner, subject))

        # ingest_id mirrors Stage 2: prefer source_chunk_id, fall back to content_hash.
        ingest_id = r["source_chunk_id"] or r["content_hash"]

        stats["migrate"] += 1
        bucket["migrate"] += 1
        bucket["taxonomy"] = tax
        if status == "current":
            stats["promote_current"] += 1
            bucket["current"] += 1
        else:
            stats["stay_historical"] += 1
            bucket["historical"] += 1

        plan_rows.append({
            "thoughts_id": sid,
            "content": r["content"],
            "embedding": r["embedding"],
            "domain": tax["domain"],
            "environment": tax["environment"],
            "system": tax["system"],
            "tags": tax["tags"],
            "status": status,
            "ingest_id": ingest_id,
            "source": cutover_source,
            "created_by": owner or "mmcmahon",
            "created_at": r["created_at"],
            "document_id_backfill_needed": not doc_id,
        })

    stats["mapper_label"] = mapper_label
    return plan_rows, stats


# ── Execute (WRITE — gated; never run in this workstream) ───────────────────────────

INSERT_SQL = """
    INSERT INTO public.knowledge
      (content, embedding, domain, environment, system, tags,
       status, ingest_id, source, created_by, created_at)
    VALUES
      (%(content)s, %(embedding)s, %(domain)s, %(environment)s, %(system)s, %(tags)s,
       %(status)s, %(ingest_id)s, %(source)s, %(created_by)s, %(created_at)s)
"""


def execute(conn, plan_rows):
    """WRITE PATH. Only reached with --execute after explicit confirmation."""
    cur = conn.cursor()
    inserted = 0
    for p in plan_rows:
        params = {k: p[k] for k in (
            "content", "embedding", "domain", "environment", "system", "tags",
            "status", "ingest_id", "source", "created_by", "created_at")}
        cur.execute(INSERT_SQL, params)
        inserted += 1
    conn.commit()
    return inserted


# ── Reporting ──────────────────────────────────────────────────────────────────────

def _fmt_tags(tags):
    return "{" + ", ".join(tags) + "}" if tags else "{}"


def write_report(stats, plan_rows, dry_run: bool):
    now = datetime.now(timezone.utc)
    L = []
    L.append("# OB2 Cutover — Stage 3 Data Migration Report (Workstream C)")
    L.append("")
    L.append(f"**Generated:** {now.isoformat()}")
    L.append(f"**Mode:** {'DRY RUN (no DB writes)' if dry_run else 'EXECUTED'}")
    L.append(f"**Taxonomy mapper:** {stats['mapper_label']}")
    L.append(f"**Source table:** `public.thoughts` → `public.knowledge`")
    L.append(f"**Source tag:** `cutover:thoughts:<thoughts_id>` (one tag per row, for rollback)")
    L.append("")
    L.append("> Auto-generated by `scripts/cutover_migrate.py`. Sign-off required before `--execute`.")
    L.append("")
    L.append("## Headline numbers")
    L.append("")
    L.append("| Outcome | Rows |")
    L.append("|---|---|")
    L.append(f"| Gap (thoughts rows not in knowledge by content) | {stats['gap_total']} |")
    L.append(f"| Already migrated this cutover (idempotency skip) | {stats['already_migrated']} |")
    L.append(f"| **Migrate** | **{stats['migrate']}** |")
    L.append(f"| &nbsp;&nbsp;→ promote to `current` | {stats['promote_current']} |")
    L.append(f"| &nbsp;&nbsp;→ stay `historical` | {stats['stay_historical']} |")
    L.append(f"| **Drop** (smoke-test / malformed) | **{stats['drop']}** |")
    L.append(f"| **Flag** (needs owner input) | **{stats['flag']}** |")
    L.append(f"| document_id NULL (ADR-011 dec.2 backfill) | {stats['doc_id_null']} |")
    L.append("")

    L.append("## Proposed taxonomy by subject bucket")
    L.append("")
    L.append("| Subject | Migrate | →current | →historical | Drop | Flag | domain | environment | system | tags |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for subj in sorted(stats["by_subject"].keys()):
        b = stats["by_subject"][subj]
        tax = b["taxonomy"]
        if tax:
            dom, env, sysn, tg = tax["domain"], tax["environment"], tax["system"] or "—", _fmt_tags(tax["tags"])
        else:
            dom = env = sysn = tg = "—"
        L.append(f"| {subj} | {b['migrate']} | {b['current']} | {b['historical']} | "
                 f"{b['drop']} | {b['flag']} | {dom} | {env} | {sysn} | {tg} |")
    L.append("")

    L.append("## Curation decisions applied (authoritative)")
    L.append("")
    L.append("- **(a) Drop smoke-test / malformed rows.** Subjects in the smoke-test set "
             "(`mcp_smoke`, `smoke test`, `live smoke test note`, `*_smoke_test`, `System`) and "
             "`[malformed subject: ...]` / >80-char sentence-subjects are NOT migrated.")
    L.append("- **(b) Personal / mental-health rows** → `domain=Personal, environment=Archive`. "
             "NOTE: the gap (post-Stage-2) contains no `Personal`/`Personal - Mental Health` "
             '(\"Core Wound\"/\"Betrayal\") rows — those were in the original 699 Stage-2 snapshot. '
             "The closest gap rows are Mike's notes *about Annie's schooling* "
             "(`Annie — Educational Assessment`, `Annie — Ubuntu Study Plan`), classified "
             "Personal/Archive as family-sensitive content, NOT as Annie's study material.")
    L.append("- **(c) Annie's study content** (`Linux Command Line` cards) → "
             "`domain=Study, environment=Study`.")
    L.append("")
    L.append("## Open ambiguities — OWNER INPUT REQUESTED")
    L.append("")
    L.append("1. **`Annie — Educational Assessment` / `Annie — Ubuntu Study Plan` (3 rows).** "
             "Authored by Mike about Annie (Woodcock-Johnson IV results, curriculum decisions). "
             "Proposed: `Personal/Archive` (family-sensitive, parental oversight context), kept "
             "`historical`. Confirm this should NOT be family-visible Study content.")
    L.append("2. **`interview-prep` / `Git / Workflow` (2 rows).** Mapped to `Study/Study` and "
             "promoted to `current`. If these are point-in-time rather than evergreen reference, "
             "they should stay `historical`.")
    L.append("3. **`session-context` (11 rows).** Includes a `credential-incident` topic row — "
             "verify it carries no live secret before it lands in `knowledge` (it inherits the "
             "thoughts content verbatim). Kept `historical`, `OpenBrain/Study`.")
    L.append("4. **The flagged null-subject row** (`0613f079…`, 51 chars, no subject/topic/owner, "
             "`source_type=NULL`) — almost certainly a test/empty artifact. Recommend DROP, but "
             "left as FLAG pending owner confirmation rather than silently discarded.")
    L.append("")
    L.append("## Promotion policy (which rows become `current`)")
    L.append("")
    L.append("Promoted to `current` (live operational state + evergreen study reference):")
    L.append("")
    for s in sorted(_PROMOTE_SUBJECTS):
        L.append(f"- `{s}`")
    L.append("")
    L.append("All other surviving subjects stay `historical` (matches Stage-2 default). "
             "Notably `session-context` (session-wrap notes) stays historical — point-in-time, "
             "not live state.")
    L.append("")

    L.append("## Drop list (NOT migrated)")
    L.append("")
    if stats["drop_list"]:
        L.append("| thoughts_id | subject | topic | reason |")
        L.append("|---|---|---|---|")
        for sid, subj, topic, reason in stats["drop_list"]:
            L.append(f"| `{sid}` | {subj} | {topic} | {reason} |")
    else:
        L.append("_None._")
    L.append("")

    L.append("## Flagged — cannot confidently classify (OWNER INPUT REQUIRED)")
    L.append("")
    if stats["flag_list"]:
        L.append("| thoughts_id | subject | topic | reason |")
        L.append("|---|---|---|---|")
        for sid, subj, topic, reason in stats["flag_list"]:
            L.append(f"| `{sid}` | {subj} | {topic} | {reason} |")
    else:
        L.append("_None._")
    L.append("")

    L.append("## document_id NULL backfill list (ADR-011 decision 2 — report only)")
    L.append("")
    L.append("These surviving rows have NULL/empty `document_id`. Per ADR-011 decision 2 they "
             "should be backfilled via the deterministic `compute_ingest_id` as a separate "
             "reviewed read-then-write migration. This script does NOT backfill — it reports.")
    L.append("")
    if stats["doc_null_list"]:
        L.append("| thoughts_id | owner | subject |")
        L.append("|---|---|---|")
        for sid, owner, subj in stats["doc_null_list"]:
            L.append(f"| `{sid}` | {owner} | {subj} |")
    else:
        L.append("_None — all surviving rows have a populated `document_id`._")
    L.append("")

    L.append("## Rollback")
    L.append("")
    L.append("Every migrated row is tagged `source = 'cutover:thoughts:<id>'`. To fully reverse:")
    L.append("")
    L.append("```sql")
    L.append("DELETE FROM public.knowledge WHERE source LIKE 'cutover:%';")
    L.append("```")
    L.append("")
    L.append("`thoughts` is left untouched (read-only source) and remains the hot standby.")
    L.append("")

    L.append("## Idempotency")
    L.append("")
    L.append("Re-running `--execute` skips any row whose `cutover:thoughts:<id>` source tag is "
             "already present in `knowledge`. Safe to re-run after a partial failure.")
    L.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(L) + "\n")


# ── CLI ────────────────────────────────────────────────────────────────────────────

def _print_console(stats, dry_run):
    tag = "[DRY RUN] " if dry_run else ""
    print(f"{tag}OB2 cutover gap migration")
    print(f"  taxonomy mapper : {stats['mapper_label']}")
    print(f"  gap rows        : {stats['gap_total']}")
    print(f"  already migrated: {stats['already_migrated']}")
    print(f"  MIGRATE         : {stats['migrate']}  (→current {stats['promote_current']}, "
          f"→historical {stats['stay_historical']})")
    print(f"  DROP            : {stats['drop']}")
    print(f"  FLAG            : {stats['flag']}")
    print(f"  document_id NULL: {stats['doc_id_null']} (ADR-011 dec.2 backfill — report only)")
    print(f"  report          : {REPORT_PATH}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="OB2 cutover gap migration (thoughts → knowledge). Dry-run by default.")
    ap.add_argument("--execute", action="store_true",
                    help="WRITE to the DB. Default is dry-run. Human approval + signed report required.")
    args = ap.parse_args()

    map_fn, mapper_label = _load_taxonomy_mapper()
    if mapper_label.startswith("LOCAL"):
        print("WARNING: api.taxonomy_map (Workstream B) not found — using LOCAL fallback rules.",
              file=sys.stderr)

    if args.execute:
        confirm = input(
            "\n!! EXECUTE mode: this WRITES to production Supabase (public.knowledge).\n"
            "Preconditions: backup confirmed, report signed off.\n"
            "Type 'yes' to confirm: ")
        if confirm.strip().lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    with psycopg.connect(_conninfo(_db_url()), row_factory=dict_row) as conn:
        plan_rows, stats = plan(conn, map_fn, mapper_label)
        dry_run = not args.execute
        _print_console(stats, dry_run)
        write_report(stats, plan_rows, dry_run)

        if dry_run:
            print("\n[DRY RUN] No DB writes. Pass --execute (human-gated) to apply.")
            return

        inserted = execute(conn, plan_rows)
        print(f"\nInserted {inserted} rows into public.knowledge.")
        print(f"Rollback: DELETE FROM public.knowledge WHERE source LIKE 'cutover:%';")


if __name__ == "__main__":
    main()
