#!/usr/bin/env python3
"""recency_baseline.py — P0 baseline for extending the recency net to `Personal`.

WHY: `_RECENCY_DOMAINS = {"Network","K8s","Security"}` (knowledge_retrieval.py) is an
allowlist, so `Personal` never decays. That is right for identity/family records and wrong
for the career pipeline sharing the domain with them. Before changing the allowlist we need
a fixed, re-runnable measurement — otherwise "it got better" is an opinion.

WHAT IT DOES (READ-ONLY — SELECT only, mutates nothing, flips no persistent env):
  1. Runs a fixed query set across three `Personal` populations (identity / career / health).
  2. Records the current top-N per query: doc id, heading, score, age, exemption reason.
  3. Computes the decay factor each row WOULD take if `Personal` joined the allowlist, and
     the resulting projected score — so the P3 delta is predicted here, not discovered later.

Run before P1/P3 to capture the baseline, and again after P3 to compare:
    python scripts/recency_baseline.py --json baseline-pre.json
    python scripts/recency_baseline.py --json baseline-post.json
    python scripts/recency_baseline.py --compare baseline-pre.json baseline-post.json

A row is EXEMPT (never decays) when it is component-keyed, `durable`-tagged, or its domain is
outside the allowlist. P1's job is to make the identity rows exempt via `durable` BEFORE P3
turns the domain on; this script shows which rows are still unprotected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_ENV = ROOT / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
os.environ.setdefault("OPENBRAIN_READ_TARGET", "knowledge")

OWNER = os.getenv("OPENBRAIN_DEFAULT_OWNER", "mike.mcmahon67")

# Three populations that share domain='Personal' but want opposite staleness behaviour.
# Keep this list STABLE — changing it invalidates comparison against an earlier baseline.
QUERY_SET: dict[str, list[str]] = {
    "identity": [
        "who is Annie and where does she go to school",
        "who is Beth",
        "where does the McMahon family live",
        "what is Mike's professional background",
    ],
    "career": [
        "what contract roles am I currently being considered for",
        "what rate was quoted for the BNSF role",
        "which recruiter submitted me for a Windows automation role",
    ],
    "health": [
        "what is my current nutrition approach",
        "recent food log entries",
    ],
}


def _decay(age_days: float, halflife: float, floor: float) -> float:
    if halflife <= 0:
        return 1.0
    return max(floor, 0.5 ** (age_days / halflife))


def _exempt_reason(row: dict) -> str | None:
    tags = row.get("tags") or []
    if any(isinstance(t, str) and t.startswith("component:") for t in tags):
        return "component-keyed"
    if "durable" in tags:
        return "durable"
    return None


def _enrich(ids: list[str]) -> dict[str, dict]:
    """Resolve retrieval ids -> {parent_id, heading, created_at}.

    `retrieve_knowledge` returns a lean row (id/domain/tags/score/signals) with no
    created_at and no heading, but age is exactly what the recency projection needs. The
    returned id may be a chunk id or a parent id depending on read target, so resolve both
    and let the caller not care. READ-ONLY.
    """
    if not ids:
        return {}
    import psycopg
    from psycopg.rows import dict_row

    out: dict[str, dict] = {}
    with psycopg.connect(os.environ["SUPABASE_DB_URL"], row_factory=dict_row) as conn:
        for row in conn.execute(
            """SELECT kc.id::text AS rid, kc.document_id::text AS parent_id,
                      kc.heading, k.created_at
                 FROM public.knowledge_chunked kc
                 JOIN public.knowledge k ON k.id = kc.document_id
                WHERE kc.id = ANY(%s)""", [ids]):
            out[row["rid"]] = row
        missing = [i for i in ids if i not in out]
        if missing:
            for row in conn.execute(
                """SELECT k.id::text AS rid, k.id::text AS parent_id,
                          NULL::text AS heading, k.created_at
                     FROM public.knowledge k WHERE k.id = ANY(%s)""", [missing]):
                out[row["rid"]] = row
    return out


def capture(n_results: int, halflife: float, floor: float) -> dict:
    from api.knowledge_retrieval import retrieve_knowledge

    now = datetime.now(timezone.utc)
    out: dict = {"owner": OWNER, "halflife": halflife, "floor": floor, "populations": {}}

    for population, queries in QUERY_SET.items():
        out["populations"][population] = []
        for q in queries:
            hits = retrieve_knowledge(q, n_results, OWNER) or []
            meta = _enrich([str(h.get("id")) for h in hits if h.get("id")])
            recorded = []
            for rank, h in enumerate(hits):
                m = meta.get(str(h.get("id")), {})
                ca = m.get("created_at")
                age = None
                if ca is not None:
                    if ca.tzinfo is None:
                        ca = ca.replace(tzinfo=timezone.utc)
                    age = max(0.0, (now - ca).total_seconds() / 86400.0)

                reason = _exempt_reason(h)
                score = h.get("score")
                # Projected: what this row's score becomes if `Personal` joins the allowlist.
                if h.get("domain") == "Personal" and reason is None and age is not None:
                    factor = _decay(age, halflife, floor)
                else:
                    factor = 1.0
                recorded.append({
                    "rank": rank,
                    "document_id": m.get("parent_id"),
                    "heading": m.get("heading"),
                    "domain": h.get("domain"),
                    "tags": h.get("tags"),
                    "age_days": round(age, 1) if age is not None else None,
                    "score": score,
                    "exempt": reason,
                    "projected_factor": round(factor, 4),
                    "projected_score": round(score * factor, 6) if score is not None else None,
                })
            out["populations"][population].append({"query": q, "hits": recorded})
    return out


def render(cap: dict) -> None:
    for population, entries in cap["populations"].items():
        print(f"\n{'='*78}\n{population.upper()}\n{'='*78}")
        for e in entries:
            print(f"\n  Q: {e['query']}")
            if not e["hits"]:
                print("     (no hits)")
            for h in e["hits"]:
                exempt = h["exempt"] or ("-" if h["domain"] == "Personal" else "off-allowlist")
                age = f"{h['age_days']:.0f}d" if h["age_days"] is not None else "?"
                arrow = "" if h["projected_factor"] == 1.0 else \
                        f"  ->  {h['projected_score']:.6f}  (x{h['projected_factor']:.3f})"
                print(f"     {h['rank']}. {h['score']:.6f}  {age:>6}  "
                      f"[{h['domain']}/{exempt}] {str(h['heading'])[:44]}{arrow}")


def compare(pre_path: str, post_path: str) -> None:
    pre = json.loads(Path(pre_path).read_text())
    post = json.loads(Path(post_path).read_text())
    for population in pre["populations"]:
        print(f"\n=== {population} ===")
        for a, b in zip(pre["populations"][population], post["populations"][population]):
            if a["query"] != b["query"]:
                print(f"  !! query set drifted: {a['query']!r} vs {b['query']!r}")
                continue
            top_a = a["hits"][0]["document_id"] if a["hits"] else None
            top_b = b["hits"][0]["document_id"] if b["hits"] else None
            flag = "  CHANGED" if top_a != top_b else ""
            print(f"  {a['query'][:56]:<56}{flag}")
            if top_a != top_b:
                ha = a["hits"][0]["heading"] if a["hits"] else "(none)"
                hb = b["hits"][0]["heading"] if b["hits"] else "(none)"
                print(f"      was: {ha}")
                print(f"      now: {hb}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--json", help="write the capture to this path")
    p.add_argument("--n-results", type=int, default=5)
    p.add_argument("--halflife", type=float,
                   default=float(os.getenv("OPENBRAIN_RECENCY_HALFLIFE_DAYS") or 90))
    p.add_argument("--floor", type=float,
                   default=float(os.getenv("OPENBRAIN_RECENCY_FLOOR") or 0.25))
    p.add_argument("--compare", nargs=2, metavar=("PRE", "POST"))
    a = p.parse_args()

    if a.compare:
        compare(*a.compare)
        return 0

    cap = capture(a.n_results, a.halflife, a.floor)
    render(cap)
    if a.json:
        Path(a.json).write_text(json.dumps(cap, indent=2, default=str))
        print(f"\nwrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
