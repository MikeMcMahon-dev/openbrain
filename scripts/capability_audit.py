#!/usr/bin/env python3
"""Capability audit — the mechanical half of the deployment-completeness gate.

Catches "capability without a caller": a schema field/param/flag that landed at one
layer but whose path to actually being *used* (ingest surface -> write -> read ->
retrieval) never completed, so it sits inert at ~0% utilisation looking deployed. This
is the pattern behind ADR-018's findings — `valid_from`, `valid_until`, `component_key`,
and `system` all shipped in OB2 and were never wired to a caller.

How it gates:
- Each capability is a (label, predicate) whose predicate is TRUE when the capability is
  exercised on a row. Utilisation = matching rows / total.
- Under FAIL_THRESHOLD and NOT in the allowlist  -> FAIL (exit 1). This is the hard block:
  a new orphaned capability cannot merge unless it is wired OR explicitly registered with
  a reason. Registering IS the completeness discipline — you must declare the gap.
- Under threshold but allowlisted            -> KNOWN (acknowledged, tracked).
- At/over threshold                          -> OK (has a caller).

The allowlist (capability_audit.allow.json) is how a known-orphan is declared: label ->
{reason, tracked_by}. It is the audit trail of every capability we shipped without a
caller and where its completion is tracked.

Run: python scripts/capability_audit.py            (report + gate, exit 1 on new orphan)
     python scripts/capability_audit.py --json      (machine-readable)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parent.parent
ALLOWLIST = Path(__file__).resolve().parent / "capability_audit.allow.json"

FAIL_THRESHOLD = 0.03   # < 3% exercised = orphaned
WARN_THRESHOLD = 0.20   # < 20% exercised = underused (report, don't gate)

# Each capability: predicate TRUE when the row exercises it. Predicates are written so a
# migration-only population (cutover:/migration: sources) does NOT count as organic use —
# a column filled once by the OB1->OB2 backfill and never since is still orphaned.
_ORGANIC = (
    "valid_from IS DISTINCT FROM created_at "
    "AND source NOT LIKE 'cutover:%' AND source NOT LIKE 'migration:%'"
)
_HAS_COMPONENT = "EXISTS (SELECT 1 FROM unnest(tags) t WHERE t LIKE 'component:%')"
CAPABILITIES = [
    ("knowledge.system",               "system IS NOT NULL"),
    ("knowledge.valid_from (organic)", _ORGANIC),
    ("knowledge.valid_until",          "valid_until IS NOT NULL"),
    ("knowledge.supersedes_id",        "supersedes_id IS NOT NULL"),
    ("knowledge.component_key(tag)",   _HAS_COMPONENT),
    ("knowledge.environment",          "environment IS NOT NULL"),
]


def _dsn() -> str:
    env = {}
    envfile = ROOT / ".env.local"
    for line in envfile.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env["SUPABASE_DB_URL"]


def _load_allowlist() -> dict:
    if ALLOWLIST.exists():
        return json.loads(ALLOWLIST.read_text())
    return {}


# ── P0 invariants (ADR-018): supersession-identity health. Handoff artifacts
# (component:adr-018) are excluded so the baseline measures the store, not our process. ──
_EXCL = "t LIKE 'component:%' AND t <> 'component:adr-018'"
INVARIANTS = [
    # null-system component rows: the unsatisfiable-identity population. Must trend to 0
    # (the real rows re-key in P2). Reported, not a hard gate.
    ("null-system component rows (-> 0)",
     f"SELECT count(*) FROM knowledge WHERE status='current' AND system IS NULL "
     f"AND EXISTS (SELECT 1 FROM unnest(tags) t WHERE {_EXCL})",
     False),
    # more than one current row per (system, component): the invariant the unique index
    # enforces once P2 lands. Must STAY 0 — a hard gate.
    (">1 current per (system,component) (=0)",
     f"SELECT count(*) FROM (SELECT 1 FROM knowledge WHERE status='current' "
     f"AND EXISTS (SELECT 1 FROM unnest(tags) t WHERE {_EXCL}) "
     f"GROUP BY system, (SELECT t FROM unnest(tags) t WHERE {_EXCL} LIMIT 1) "
     f"HAVING count(*) > 1) v",
     True),
]


def invariants() -> list[dict]:
    out = []
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        for label, sql, is_gate in INVARIANTS:
            cur.execute(sql)
            n = cur.fetchone()[0]
            out.append({"invariant": label, "count": n, "gate": is_gate,
                        "violated": is_gate and n > 0})
    return out


def audit() -> list[dict]:
    allow = _load_allowlist()
    rows = []
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM knowledge")
        total = cur.fetchone()[0] or 1
        for label, pred in CAPABILITIES:
            cur.execute(f"SELECT count(*) FROM knowledge WHERE {pred}")
            n = cur.fetchone()[0]
            frac = n / total
            if frac >= WARN_THRESHOLD:
                verdict = "ok"
            elif frac >= FAIL_THRESHOLD:
                verdict = "known" if label in allow else "underused"
            else:
                verdict = "known" if label in allow else "ORPHANED"
            rows.append({
                "capability": label, "used": n, "total": total,
                "pct": round(100 * frac, 1), "verdict": verdict,
                "note": allow.get(label, {}).get("reason", ""),
                "tracked_by": allow.get(label, {}).get("tracked_by", ""),
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    results = audit()
    inv = invariants()
    new_orphans = [r for r in results if r["verdict"] == "ORPHANED"]
    inv_violations = [i for i in inv if i["violated"]]

    if args.json:
        out = {"results": results, "invariants": inv,
               "new_orphans": [r["capability"] for r in new_orphans],
               "invariant_violations": [i["invariant"] for i in inv_violations]}
        print(json.dumps(out, indent=2))
    else:
        print("=== capability audit (deployment-completeness gate) ===\n")
        tags = {"ok": "  ok ", "underused": " WARN", "known": "known", "ORPHANED": "FAIL!"}
        for r in results:
            extra = f"  [{r['tracked_by']}]" if r["tracked_by"] else ""
            print(f"  [{tags[r['verdict']]}] {r['capability']:34} "
                  f"{r['used']:4}/{r['total']} ({r['pct']:4}%){extra}")
        print("\n  P0 invariants (ADR-018):")
        for i in inv:
            mark = "FAIL!" if i["violated"] else ("gate " if i["gate"] else " mon ")
            print(f"  [{mark}] {i['invariant']:38} = {i['count']}")
        if new_orphans:
            print(f"\nFAIL: {len(new_orphans)} capability(ies) landed with no caller and are not "
                  f"registered:\n  " + "\n  ".join(r["capability"] for r in new_orphans))
            print("\nWire it, or register it in capability_audit.allow.json "
                  "with a reason + tracking.")
        if inv_violations:
            print(f"\nFAIL: {len(inv_violations)} P0 invariant(s) violated: "
                  + ", ".join(i["invariant"] for i in inv_violations))
        if not new_orphans and not inv_violations:
            print("\nPASS: no unregistered orphaned capabilities; P0 invariants hold.")
    return 1 if (new_orphans or inv_violations) else 0


if __name__ == "__main__":
    sys.exit(main())
