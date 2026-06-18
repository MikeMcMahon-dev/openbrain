#!/usr/bin/env python3
"""One-time normalization of existing public.knowledge rows to the ADR-012 canonical
vocabulary. Dry-run by default; real writes only behind --execute (human-gated).

Operations (per row):
  1. Normalize tags through api.taxonomy_map.normalize_tags (folds HomeLab->Homelab,
     Sessions->Session, MentalHealth->Mental-health; drops retired SmokeTest).
  2. Add Career + Interview tags to interview/career content (so it retrieves as a set).
  3. Re-flag mental-health rows -> domain=Personal, environment=Archive, tags include
     Personal + Mental-health.
  4. DELETE pure smoke-test rows (tag SmokeTest = retired artifacts; owner-confirmed drop).

MUST run BEFORE migrations/003_tag_vocabulary.sql installs the validation trigger, else
the trigger rejects the existing drifted tags. Reversible via the pre-cutover backup
(~/ob_backup_*/knowledge.copy) and/or: the script tags nothing it can't re-derive.
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
from api.taxonomy_map import normalize_tags  # noqa: E402

_INTERVIEW_MARKERS = ("interview", "stories bank", "positioning", "ammunition", "star method")
_MENTAL_MARKERS = ("core wound", "betrayal", "mental health")


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


def plan_row(row: dict) -> dict | None:
    """Return a change dict for this row, or None if no change."""
    rid = str(row["id"])
    tags = list(row.get("tags") or [])
    content = (row.get("content") or "").lower()
    domain, environment = row.get("domain"), row.get("environment")

    # 4. pure smoke-test artifact -> delete
    if "SmokeTest" in tags:
        return {"id": rid, "action": "delete", "reason": "SmokeTest artifact (retired)"}

    new_tags, unknown = normalize_tags(tags)

    # Mental-health takes precedence over career tagging (mirrors the mapper's ordering):
    # a Core Wound session must not be filed as interview material even if its content
    # mentions interviews.
    is_mental = any(m in content for m in _MENTAL_MARKERS)

    new_domain, new_env = domain, environment
    if is_mental:
        # 3. mental-health re-flag
        new_domain, new_env = "Personal", "Archive"
        for t in ("Personal", "Mental-health"):
            if t not in new_tags:
                new_tags.append(t)
    elif any(m in content for m in _INTERVIEW_MARKERS):
        # 2. interview/career content -> add Career + Interview
        for t in ("Career", "Interview"):
            if t not in new_tags:
                new_tags.append(t)

    changed = (new_tags != tags) or (new_domain != domain) or (new_env != environment)
    if not changed:
        return None
    return {
        "id": rid, "action": "update",
        "old_tags": tags, "new_tags": new_tags,
        "domain": (domain, new_domain), "environment": (environment, new_env),
        "unknown": unknown,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="apply writes (default: dry-run)")
    args = ap.parse_args()

    with psycopg.connect(conninfo(), row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT id, domain, environment, tags, content FROM public.knowledge"
        ).fetchall()

        updates, deletes = [], []
        for r in rows:
            ch = plan_row(r)
            if ch is None:
                continue
            (deletes if ch["action"] == "delete" else updates).append(ch)

        print(f"{'EXECUTE' if args.execute else 'DRY RUN'}: {len(rows)} rows scanned")
        print(f"  updates: {len(updates)}   deletes: {len(deletes)}\n")
        for u in updates[:60]:
            d0, d1 = u["domain"]; e0, e1 = u["environment"]
            dom = f"  domain {d0}->{d1}" if d0 != d1 else ""
            env = f"  env {e0}->{e1}" if e0 != e1 else ""
            print(f"  UPDATE {u['id'][:8]} tags {u['old_tags']} -> {u['new_tags']}{dom}{env}")
            if u["unknown"]:
                print(f"         (unknown tags, NOT applied — queue for review: {u['unknown']})")
        for d in deletes:
            print(f"  DELETE {d['id'][:8]}  {d['reason']}")

        if not args.execute:
            print("\nDry run only. Re-run with --execute to apply (after backup; before the 003 trigger).")
            return

        for u in updates:
            conn.execute(
                "UPDATE public.knowledge SET tags=%s, domain=%s, environment=%s WHERE id=%s",
                [u["new_tags"], u["domain"][1], u["environment"][1], u["id"]],
            )
        for d in deletes:
            conn.execute("DELETE FROM public.knowledge WHERE id=%s", [d["id"]])
        print(f"\nApplied: {len(updates)} updates, {len(deletes)} deletes.")


if __name__ == "__main__":
    main()
