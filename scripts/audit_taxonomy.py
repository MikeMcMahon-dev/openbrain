#!/usr/bin/env python3
"""Periodic taxonomy drift-audit for OpenBrain (ADR-012). READ-ONLY.

Reports the taxonomy health of ``public.knowledge`` against the single source of
truth in ``api/taxonomy_map.py`` (CANONICAL_TAGS, TAG_ALIASES, normalize_tags).

The vocabulary is NOT redefined here — it is imported. This script only observes
the live data, classifies it through ``normalize_tags``, detects near-duplicate
drift the alias table hasn't caught yet, and emits a verdict + suggested actions.

What it checks:
  1. Distinct tags in ``knowledge.tags`` with row counts.
  2. Classification of each tag via ``normalize_tags``:
       canonical / aliased (with fold target) / unknown (drift).
  3. Near-duplicate detection: tags whose ``_tag_key`` collides with, or is
     edit-distance-1 from, a canonical tag but isn't exactly canonical — catches
     casing/typo drift not yet aliased.
  4. domain / environment values vs the allowed enums; flag out-of-enum.
  5. ``system`` value distribution.
  6. Summary verdict: clean vs drifted rows + a suggested-actions list.

DB access is strictly SELECT-only. Connection string is read from ``.env.local``
(``SUPABASE_DB_URL``); no credentials are hardcoded.

Usage:
    .venv/bin/python scripts/audit_taxonomy.py
Writes the report to docs/migrations/004_taxonomy_audit_report.md and stdout.
"""
from __future__ import annotations

import datetime as _dt
import io
import sys
import urllib.parse
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

# Make the repo root importable so ``api.taxonomy_map`` resolves regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Single source of truth — DO NOT re-define the vocabulary here.
from api.taxonomy_map import (
    CANONICAL_TAGS,
    TAG_ALIASES,
    VALID_DOMAINS,
    VALID_ENVIRONMENTS,
    _tag_key,
    normalize_tags,
)

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "docs" / "migrations" / "004_taxonomy_audit_report.md"


# ── DB connection (read-only) ───────────────────────────────────────────────
def db_url() -> str:
    """Read SUPABASE_DB_URL from .env.local (pattern from ingest/diag_retrieval.py)."""
    for line in (ROOT / ".env.local").read_text().splitlines():
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


# ── edit distance (for near-duplicate / typo drift) ─────────────────────────
def _within_edit_distance_1(a: str, b: str) -> bool:
    """True iff Levenshtein(a, b) <= 1. Cheap early-exit, no full matrix needed."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:  # at most one substitution
        return sum(1 for x, y in zip(a, b) if x != y) == 1
    # length differs by exactly one -> check for a single insertion/deletion
    if la > lb:
        a, b = b, a
        la, lb = lb, la
    i = j = 0
    edited = False
    while i < la and j < lb:
        if a[i] != b[j]:
            if edited:
                return False
            edited = True
            j += 1  # skip the extra char in the longer string
        else:
            i += 1
            j += 1
    return True


def classify_tag(tag: str) -> tuple[str, str | None]:
    """Return (classification, fold_target).

    classification in {canonical, aliased, dropped, unknown}.
    Uses normalize_tags as the authority (single tag -> its result).
    """
    key = _tag_key(tag)
    if not key:
        return ("empty", None)
    # Explicit alias table first (matches normalize_tags ordering).
    if key in TAG_ALIASES:
        target = TAG_ALIASES[key]
        if target is None:
            return ("dropped", None)
        # An alias whose target normalizes back to the same key is a no-op fold
        # (the stored surface form is already the canonical form) -> treat canonical.
        if _tag_key(target) == key:
            return ("canonical", target)
        return ("aliased", target)
    canonical, unknown = normalize_tags([tag])
    if canonical:
        folded = canonical[0]
        # Exact canonical (case-insensitive key match) vs casing-fold.
        if tag == folded:
            return ("canonical", folded)
        return ("aliased", folded)  # folded by _CANON_BY_KEY (e.g. casing)
    return ("unknown", None)


# ── report buffer (tee to stdout + .md) ──────────────────────────────────────
class Tee:
    def __init__(self) -> None:
        self.buf = io.StringIO()

    def line(self, s: str = "") -> None:
        print(s)
        self.buf.write(s + "\n")

    def value(self) -> str:
        return self.buf.getvalue()


def main() -> None:
    out = Tee()
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    out.line("# OpenBrain Taxonomy Drift Audit — public.knowledge")
    out.line()
    out.line(f"_Generated: {now} (READ-ONLY; SELECT only)_")
    out.line()
    out.line("Source of truth: `api/taxonomy_map.py` "
             "(CANONICAL_TAGS, TAG_ALIASES, normalize_tags). ADR-012.")
    out.line()

    with psycopg.connect(conninfo(db_url()), row_factory=dict_row) as conn:
        # ── corpus size ──
        total_rows = conn.execute(
            "SELECT count(*) c FROM public.knowledge"
        ).fetchone()["c"]

        # ── 1. distinct tags + counts (unnest the array) ──
        tag_rows = conn.execute(
            """
            SELECT t AS tag, count(*) AS n
            FROM public.knowledge, unnest(tags) AS t
            GROUP BY t
            ORDER BY n DESC, t
            """
        ).fetchall()

        # sample row id per tag (for the unknown drift report)
        tag_sample = {
            r["tag"]: r["sample_id"]
            for r in conn.execute(
                """
                SELECT DISTINCT ON (t) t AS tag, id::text AS sample_id
                FROM public.knowledge, unnest(tags) AS t
                ORDER BY t, id
                """
            ).fetchall()
        }

        # rows carrying at least one tag / no tags
        rows_with_tags = conn.execute(
            "SELECT count(*) c FROM public.knowledge "
            "WHERE tags IS NOT NULL AND array_length(tags,1) >= 1"
        ).fetchone()["c"]

        # ── 4. domain / environment distributions ──
        domain_rows = conn.execute(
            "SELECT COALESCE(domain,'<null>') AS v, count(*) n "
            "FROM public.knowledge GROUP BY 1 ORDER BY n DESC"
        ).fetchall()
        env_rows = conn.execute(
            "SELECT COALESCE(environment,'<null>') AS v, count(*) n "
            "FROM public.knowledge GROUP BY 1 ORDER BY n DESC"
        ).fetchall()

        # ── 5. system distribution ──
        system_rows = conn.execute(
            "SELECT COALESCE(system,'<null>') AS v, count(*) n "
            "FROM public.knowledge GROUP BY 1 ORDER BY n DESC"
        ).fetchall()

    # ── classify tags ──
    canonical_tags: list[tuple[str, int]] = []
    aliased_tags: list[tuple[str, int, str]] = []     # (tag, n, fold_target)
    dropped_tags: list[tuple[str, int]] = []
    unknown_tags: list[tuple[str, int, str]] = []     # (tag, n, sample_id)

    for r in tag_rows:
        tag, n = r["tag"], r["n"]
        kind, target = classify_tag(tag)
        if kind == "canonical":
            canonical_tags.append((tag, n))
        elif kind == "aliased":
            aliased_tags.append((tag, n, target or "?"))
        elif kind == "dropped":
            dropped_tags.append((tag, n))
        else:  # unknown / empty
            unknown_tags.append((tag, n, tag_sample.get(tag, "?")))

    # ── 3. near-duplicate detection (key collision or edit-distance-1) ──
    # Catches casing/typo drift among tags that are NOT exactly canonical and not
    # yet covered by TAG_ALIASES. We compare the tag's key against canonical keys.
    canon_keys = {_tag_key(t): t for t in CANONICAL_TAGS}
    near_dups: list[tuple[str, int, str, str]] = []  # (tag, n, nearest_canon, why)
    aliased_or_dropped_keys = set(TAG_ALIASES.keys())
    for r in tag_rows:
        tag, n = r["tag"], r["n"]
        key = _tag_key(tag)
        if not key:
            continue
        # Skip anything that already resolves cleanly: exact canonical, casing-fold
        # via _CANON_BY_KEY (key in canon_keys), or covered by the alias table. These
        # are NOT drift requiring a new alias — they fold automatically already.
        if key in canon_keys or key in aliased_or_dropped_keys:
            continue
        # Surviving tags are genuinely unaliased. Flag edit-distance-1 typo drift
        # against any canonical key — likely typos a new alias should catch.
        for ck, canon in canon_keys.items():
            if _within_edit_distance_1(key, ck):
                near_dups.append((tag, n, canon, f"edit-distance-1 to '{canon}'"))
                break

    # ── out-of-enum domain / environment ──
    bad_domains = [(r["v"], r["n"]) for r in domain_rows
                   if r["v"] != "<null>" and r["v"] not in VALID_DOMAINS]
    null_domain = next((r["n"] for r in domain_rows if r["v"] == "<null>"), 0)
    bad_envs = [(r["v"], r["n"]) for r in env_rows
                if r["v"] != "<null>" and r["v"] not in VALID_ENVIRONMENTS]
    null_env = next((r["n"] for r in env_rows if r["v"] == "<null>"), 0)

    # ── drifted-row accounting ──
    # A row is "drifted" if it carries any unknown tag, or its domain/environment
    # is out-of-enum (null counted separately as "incomplete"). Use the DB to count
    # rows touched by unknown tags precisely (a row may carry several).
    unknown_tag_set = [t for t, _n, _s in unknown_tags]
    with psycopg.connect(conninfo(db_url()), row_factory=dict_row) as conn:
        if unknown_tag_set:
            rows_with_unknown = conn.execute(
                "SELECT count(*) c FROM public.knowledge "
                "WHERE tags && %s::text[]",
                (unknown_tag_set,),
            ).fetchone()["c"]
        else:
            rows_with_unknown = 0
        bad_dom_vals = [v for v, _ in bad_domains]
        bad_env_vals = [v for v, _ in bad_envs]
        rows_bad_enum = conn.execute(
            "SELECT count(*) c FROM public.knowledge "
            "WHERE domain = ANY(%s) OR environment = ANY(%s)",
            (bad_dom_vals, bad_env_vals),
        ).fetchone()["c"]
        # drifted = touched by any unknown tag OR out-of-enum domain/env
        drifted_rows = conn.execute(
            "SELECT count(*) c FROM public.knowledge "
            "WHERE tags && %s::text[] OR domain = ANY(%s) OR environment = ANY(%s)",
            (unknown_tag_set or [""], bad_dom_vals, bad_env_vals),
        ).fetchone()["c"]
    clean_rows = total_rows - drifted_rows

    # ── render report ──
    out.line("## Corpus")
    out.line()
    out.line(f"- Total rows: **{total_rows}**")
    out.line(f"- Rows carrying >=1 tag: **{rows_with_tags}**")
    out.line(f"- Distinct tags in use: **{len(tag_rows)}**")
    out.line(f"- Canonical vocabulary size: {len(CANONICAL_TAGS)} "
             f"(+{len(TAG_ALIASES)} alias keys)")
    out.line()

    out.line("## 1 + 2. Tag classification (via normalize_tags)")
    out.line()
    out.line(f"- Canonical: **{len(canonical_tags)}** distinct")
    out.line(f"- Aliased / casing-folded: **{len(aliased_tags)}** distinct")
    out.line(f"- Retired (drop alias, still present): **{len(dropped_tags)}** distinct")
    out.line(f"- Unknown (drift): **{len(unknown_tags)}** distinct")
    out.line()

    out.line("### Canonical tags (in use)")
    out.line()
    if canonical_tags:
        out.line("| tag | rows |")
        out.line("|---|---|")
        for tag, n in canonical_tags:
            out.line(f"| `{tag}` | {n} |")
    else:
        out.line("_none_")
    out.line()

    out.line("### Aliased / casing-folded tags (would fold on re-normalize)")
    out.line()
    if aliased_tags:
        out.line("| tag (as stored) | rows | folds to |")
        out.line("|---|---|---|")
        for tag, n, target in aliased_tags:
            out.line(f"| `{tag}` | {n} | `{target}` |")
    else:
        out.line("_none_")
    out.line()

    if dropped_tags:
        out.line("### Retired tags still present (should be removed)")
        out.line()
        out.line("| tag | rows |")
        out.line("|---|---|")
        for tag, n in dropped_tags:
            out.line(f"| `{tag}` | {n} |")
        out.line()

    out.line("### Unknown tags — DRIFT (not canonical, not aliased)")
    out.line()
    if unknown_tags:
        out.line("| tag | rows | sample row id |")
        out.line("|---|---|---|")
        for tag, n, sid in unknown_tags:
            out.line(f"| `{tag}` | {n} | `{sid}` |")
    else:
        out.line("_none — no drift_")
    out.line()

    out.line("## 3. Near-duplicate detection (casing/typo drift not yet aliased)")
    out.line()
    if near_dups:
        out.line("| tag | rows | nearest canonical | reason |")
        out.line("|---|---|---|---|")
        for tag, n, canon, why in near_dups:
            out.line(f"| `{tag}` | {n} | `{canon}` | {why} |")
    else:
        out.line("_none — no near-duplicate drift detected_")
    out.line()

    out.line("## 4. domain / environment vs allowed enums")
    out.line()
    out.line(f"Allowed domains: {sorted(VALID_DOMAINS)}")
    out.line(f"Allowed environments: {sorted(VALID_ENVIRONMENTS)}")
    out.line()
    out.line("### domain distribution")
    out.line()
    out.line("| domain | rows | status |")
    out.line("|---|---|---|")
    for r in domain_rows:
        v, n = r["v"], r["n"]
        if v == "<null>":
            status = "NULL (incomplete)"
        elif v in VALID_DOMAINS:
            status = "ok"
        else:
            status = "OUT-OF-ENUM"
        out.line(f"| `{v}` | {n} | {status} |")
    out.line()
    out.line("### environment distribution")
    out.line()
    out.line("| environment | rows | status |")
    out.line("|---|---|---|")
    for r in env_rows:
        v, n = r["v"], r["n"]
        if v == "<null>":
            status = "NULL (incomplete)"
        elif v in VALID_ENVIRONMENTS:
            status = "ok"
        else:
            status = "OUT-OF-ENUM"
        out.line(f"| `{v}` | {n} | {status} |")
    out.line()

    out.line("## 5. system value distribution")
    out.line()
    out.line("| system | rows |")
    out.line("|---|---|")
    for r in system_rows:
        out.line(f"| `{r['v']}` | {r['n']} |")
    out.line()

    # ── 6. verdict + suggested actions ──
    out.line("## 6. Verdict")
    out.line()
    out.line(f"- Clean rows: **{clean_rows}** / {total_rows}")
    out.line(f"- Drifted rows (unknown tag or out-of-enum domain/env): "
             f"**{drifted_rows}**")
    out.line(f"  - rows touched by an unknown tag: {rows_with_unknown}")
    out.line(f"  - rows with out-of-enum domain/environment: {rows_bad_enum}")
    out.line(f"- Incomplete (NULL domain): {null_domain} | "
             f"(NULL environment): {null_env}")
    out.line()

    actions: list[str] = []
    for tag, n, target in aliased_tags:
        actions.append(
            f"NORMALIZE: `{tag}` ({n} rows) already folds to `{target}` "
            f"via taxonomy_map — re-run retag to persist."
        )
    for tag, n in dropped_tags:
        actions.append(
            f"REMOVE: `{tag}` ({n} rows) is a retired drop-alias — strip from rows."
        )
    for tag, n, canon, why in near_dups:
        actions.append(
            f"ALIAS: add `_tag_key('{tag}')` -> `{canon}` to TAG_ALIASES "
            f"({why}; {n} rows)."
        )
    for tag, n, sid in unknown_tags:
        # near-dup unknowns already got an ALIAS suggestion; the rest are approve-or-alias
        if any(tag == nd[0] for nd in near_dups):
            continue
        actions.append(
            f"REVIEW: `{tag}` ({n} rows; e.g. id {sid}) is unknown drift — "
            f"approve into CANONICAL_TAGS or alias it."
        )
    for v, n in bad_domains:
        actions.append(f"FIX domain: `{v}` ({n} rows) is out-of-enum — remap to a "
                       f"valid domain {sorted(VALID_DOMAINS)}.")
    for v, n in bad_envs:
        actions.append(f"FIX environment: `{v}` ({n} rows) is out-of-enum — remap to "
                       f"{sorted(VALID_ENVIRONMENTS)}.")
    if null_domain:
        actions.append(f"BACKFILL: {null_domain} rows have NULL domain.")
    if null_env:
        actions.append(f"BACKFILL: {null_env} rows have NULL environment.")

    out.line("### Suggested actions")
    out.line()
    if actions:
        for a in actions:
            out.line(f"- {a}")
    else:
        out.line("- None. Taxonomy is clean.")
    out.line()

    verdict = "CLEAN" if drifted_rows == 0 and null_domain == 0 and null_env == 0 \
        else ("DRIFT DETECTED" if drifted_rows else "INCOMPLETE (nulls only)")
    out.line(f"**Overall: {verdict}**")
    out.line()

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(out.value())
    print(f"\n[written] {REPORT_PATH}")


if __name__ == "__main__":
    main()
