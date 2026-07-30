#!/usr/bin/env python3
"""
OpenBrain Knowledge Retrieval — Signal & Landmine Harness (ADR-014)
===================================================================
Measures RETRIEVAL SIGNAL over public.knowledge and guards the invariants that a
tuning change could quietly break ("landmines"). This is the instrument the fused
RRF score cannot be: RRF fuses on rank alone, so its output is a fixed reciprocal
per position and carries no relevance information. The per-retriever raw signals
(vector cosine similarity, lexical ts_rank) exposed via result["signals"] are what
this harness reads.

Two things run here:

  1. MEASUREMENT  — query across owners/domains, record raw signals, and report
                    separation metrics (top-1 vs rank-5 vector similarity, retriever
                    overlap, whether authoritative component:* docs win). No pass/fail;
                    this is the baseline you tune against.

  2. LANDMINES    — invariant assertions that must hold regardless of corpus content:
                    owner isolation, status default, the OB1 response-shim contract,
                    signal sanity, suppression behavior, and boost safety. Exit 1 on
                    any breach.

Safety: READ-ONLY (SELECT only). Never mutates the store. Forces
OPENBRAIN_READ_TARGET=knowledge for the measured path but touches no write path and
flips no persistent env. Run locally against the live vault per the approved
"local + eval harness only" posture.

Usage:
    python scripts/knowledge_signal_harness.py                 # measure + landmines
    python scripts/knowledge_signal_harness.py --landmines-only
    python scripts/knowledge_signal_harness.py --sweep         # boost weight sweep
    python scripts/knowledge_signal_harness.py --json out.json # machine-readable dump
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")

# The measured path is OB2 by definition; force it before importing the API so the
# dispatch in retrieve_for_query routes to knowledge. This sets the process env only.
os.environ["OPENBRAIN_READ_TARGET"] = "knowledge"

from api import knowledge_retrieval as kr  # noqa: E402
from api._openbrain_api import get_db_conn, retrieve_for_query  # noqa: E402

# ---------------------------------------------------------------------------
# Query set — grounded in the real corpus (discovered 2026-07-30):
#   owners: mike.mcmahon67 (infra/OpenBrain), anneliesepaige (Study/biology)
#   component:* current docs: dns-current-state, vlan-switch-topology (SpectreNet)
# `kind`:
#   relevant  — expect a real in-domain hit; measures the signal ceiling
#   component — should surface the authoritative component:* current doc (boost target)
#   noise     — deliberately out-of-corpus for that owner; measures the noise FLOOR
#               (top-1 similarity here should sit well below `relevant` queries)
# ---------------------------------------------------------------------------
_M = "mike.mcmahon67"
_A = "anneliesepaige"
QUERIES = [
    {"q": "spectrenet dns current architecture coredns authoritative",
     "owner": _M, "kind": "component", "component": "dns-current-state"},
    {"q": "vlan switch topology trunk ports",
     "owner": _M, "kind": "component", "component": "vlan-switch-topology"},
    {"q": "ansible playbook block rescue resiliency patterns", "owner": _M, "kind": "relevant"},
    {"q": "proxmox nested virtualization enable", "owner": _M, "kind": "relevant"},
    {"q": "terraform infrastructure as code state", "owner": _M, "kind": "relevant"},
    {"q": "kubernetes etcd raft leader election", "owner": _M, "kind": "relevant"},
    {"q": "levels of taxonomy domain kingdom phylum", "owner": _A, "kind": "relevant"},
    {"q": "what is an autotroph", "owner": _A, "kind": "relevant"},
    {"q": "mitosis cell division phases", "owner": _A, "kind": "relevant"},
    # Noise floor: each owner queried for the OTHER's domain — the store should not
    # return a confident hit, and top-1 vector similarity should be low.
    {"q": "photosynthesis chloroplast light reaction", "owner": _M, "kind": "noise"},
    {"q": "hashicorp vault unseal proxmox terraform", "owner": _A, "kind": "noise"},
]

# The cross-tenant boundary we assert never leaks (Annie must never see Mike's rows).
_TENANT_A = "anneliesepaige"
_TENANT_B = "mike.mcmahon67"

# OB1 response-shim keys the tutor packet + Custom GPTs depend on. Regression guard:
# the instrument must remain purely additive and never drop these.
_SHIM_KEYS = ("document_id", "source", "section", "heading", "content_type", "owner")


# ---------------------------------------------------------------------------
# Corpus discovery
# ---------------------------------------------------------------------------
def discover_corpus() -> dict:
    def _counts(conn, col):
        sql = (f"select {col} k, count(*) n from public.knowledge "
               "group by 1 order by 2 desc")
        return {r["k"]: r["n"] for r in conn.execute(sql).fetchall()}

    with get_db_conn() as c:
        total = c.execute("select count(*) n from public.knowledge").fetchone()["n"]
        owners = _counts(c, "created_by")
        statuses = _counts(c, "status")
        domains = _counts(c, "domain")
        wcs = [r["wc"] for r in c.execute(
            "select array_length(regexp_split_to_array(coalesce(content,''), '\\s+'),1) wc "
            "from public.knowledge").fetchall() if r["wc"]]
        components = [dict(r) for r in c.execute(
            "select created_by, system, status, tags from public.knowledge "
            "where exists (select 1 from unnest(tags) t where t like 'component:%') "
            "order by created_by, status").fetchall()]
    wc_pcts = {}
    if wcs:
        wcs.sort()
        wc_pcts = {
            "min": wcs[0], "p50": statistics.median(wcs),
            "p90": wcs[int(len(wcs) * 0.9)], "max": wcs[-1],
            "under_30w": sum(1 for w in wcs if w < 30),
        }
    return {"total": total, "owners": owners, "statuses": statuses,
            "domains": domains, "word_count": wc_pcts, "components": components}


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------
def _fnum(v):
    return None if v is None else round(float(v), 5)


def measure_query(spec: dict, n: int = 5) -> dict:
    """Run one query over ALL statuses (so stale/superseded rows can compete — the
    condition the boost must fix) and capture the raw signals per result."""
    q, owner = spec["q"], spec["owner"]
    rows = kr.retrieve_knowledge(q, n, owner, filters={"status": None})
    per = []
    for i, r in enumerate(rows):
        s = r.get("signals", {})
        tags = r.get("tags") or []
        per.append({
            "rank": i,
            "vector_similarity": _fnum(s.get("vector_similarity")),
            "lexical_score": _fnum(s.get("lexical_score")),
            "retrievers_hit": s.get("retrievers_hit"),
            "length_penalty": _fnum(s.get("length_penalty_applied")),
            "component_boost": _fnum(s.get("component_boost_applied")),
            "status": r.get("status"),
            "is_component": any(isinstance(t, str) and t.startswith("component:") for t in tags),
            "confidence": r.get("confidence"),
            "preview": (r.get("text", "")[:60]).replace("\n", " "),
        })

    vsims = [p["vector_similarity"] for p in per if p["vector_similarity"] is not None]
    top1_vsim = per[0]["vector_similarity"] if per else None
    rank5_vsim = per[-1]["vector_similarity"] if per else None
    # Rank of the first CURRENT component doc, if any surfaced (boost effectiveness).
    comp_rank = next(
        (p["rank"] for p in per if p["is_component"] and p["status"] == "current"), None)

    return {
        "query": q, "owner": owner, "kind": spec["kind"], "n": len(per),
        "top1_vsim": top1_vsim,
        "top1_confidence": per[0]["confidence"] if per else None,
        "rank1_rank5_gap": (round(top1_vsim - rank5_vsim, 5)
                            if (top1_vsim is not None and rank5_vsim is not None) else None),
        "mean_vsim": round(statistics.mean(vsims), 5) if vsims else None,
        "overlap_count": sum(1 for p in per if p["retrievers_hit"] == 2),
        "component_current_rank": comp_rank,
        "results": per,
    }


def run_measurement(n: int = 5) -> list[dict]:
    return [measure_query(spec, n) for spec in QUERIES]


# ---------------------------------------------------------------------------
# Landmine invariants — each returns (name, ok: bool, detail: str)
# ---------------------------------------------------------------------------
def inv_owner_isolation() -> tuple[str, bool, str]:
    """Annie's queries must never return a row owned by Mike (cross-tenant leak)."""
    leaks = []
    for q in ("dns architecture", "proxmox terraform ansible", "vault unseal"):
        for r in retrieve_for_query(q, 5, _TENANT_A, "family"):
            ownr = r.get("owner")
            if ownr not in (_TENANT_A, None):
                leaks.append((q, ownr, (r.get("text", "")[:40])))
    return ("owner_isolation", not leaks,
            "no cross-tenant rows" if not leaks else f"LEAK: {leaks[:3]}")


def inv_status_default() -> tuple[str, bool, str]:
    """Default filters (status omitted) must return only status='current'."""
    bad = []
    for r in kr.retrieve_knowledge("dns network architecture", 8, _TENANT_B):
        if r.get("status") != "current":
            bad.append(r.get("status"))
    return ("status_default_current", not bad,
            "only current" if not bad else f"non-current leaked: {set(bad)}")


def inv_shim_contract() -> tuple[str, bool, str]:
    """The adapted response must still carry every OB1 shim key + the new signals."""
    rows = retrieve_for_query("ansible playbook", 3, _TENANT_B, "family")
    if not rows:
        return ("shim_contract", False, "no rows returned to check")
    r = rows[0]
    missing = [k for k in _SHIM_KEYS if k not in r]
    has_signals = "signals" in r and isinstance(r["signals"], dict)
    ok = not missing and has_signals
    return ("shim_contract", ok,
            "all shims + signals present" if ok
            else f"missing shims={missing} signals={has_signals}")


def inv_signal_sanity() -> tuple[str, bool, str]:
    """Every returned signal bucket must be internally coherent."""
    problems = []
    for spec in QUERIES:
        for r in kr.retrieve_knowledge(spec["q"], 5, spec["owner"], filters={"status": None}):
            s = r.get("signals", {})
            hit = s.get("retrievers_hit")
            if hit not in (1, 2):
                problems.append(f"retrievers_hit={hit}")
            vs = s.get("vector_similarity")
            if vs is not None and not (-1.01 <= vs <= 1.01):
                problems.append(f"vsim out of range {vs}")
            if (s.get("rrf_score") or 0) <= 0:
                problems.append("rrf_score<=0")
            if s.get("vector_rank") is not None and vs is None:
                problems.append("vector_rank set but vsim None")
    return ("signal_sanity", not problems,
            "coherent" if not problems else f"{problems[:3]}")


def inv_suppression() -> tuple[str, bool, str]:
    """Setting a floor must drop below-floor vector matches WITHOUT nuking a good
    query to empty. Uses a noise query (should suppress) and a strong query (must
    survive). Restores the module knob afterward."""
    saved = kr._VECTOR_SUPPRESSION_FLOOR
    try:
        # A floor above typical noise similarity but below a strong in-domain hit.
        kr._VECTOR_SUPPRESSION_FLOOR = 0.35
        noise = kr.retrieve_knowledge("photosynthesis chloroplast", 5, _TENANT_B,
                                      filters={"status": None})
        strong = kr.retrieve_knowledge("ansible playbook block rescue", 5, _TENANT_B,
                                       filters={"status": None})
        # Any surviving vector-scored row must clear the floor.
        below = [r["signals"]["vector_similarity"] for r in noise + strong
                 if r["signals"].get("vector_similarity") is not None
                 and r["signals"]["vector_similarity"] < 0.35]
        ok = not below and len(strong) > 0
        detail = (f"floor honored; strong survived ({len(strong)} rows), "
                  f"noise trimmed to {len(noise)}") if ok else f"below-floor leaked: {below[:3]}"
    finally:
        kr._VECTOR_SUPPRESSION_FLOOR = saved
    return ("suppression_floor", ok, detail)


def inv_boost_safety() -> tuple[str, bool, str]:
    """A boost>1 must:
      (a) lift a status='current' component:* doc's fused score,
      (b) leave non-component rows untouched,
      (c) NEVER boost a superseded/historical component:* doc — those carry the same
          tag, and lifting them re-buries the live doc (the sweep landmine, 2026-07-30).
    Compares boosted vs unboosted for the DNS query. Restores the knob afterward."""
    saved = kr._COMPONENT_BOOST
    try:
        kr._COMPONENT_BOOST = 1.0
        base = {r["id"]: r["score"] for r in kr.retrieve_knowledge(
            "spectrenet dns current architecture", 8, _TENANT_B, filters={"status": None})}
        kr._COMPONENT_BOOST = 3.0
        boosted = kr.retrieve_knowledge(
            "spectrenet dns current architecture", 8, _TENANT_B, filters={"status": None})
        lifted, touched_noncomp, boosted_stale = False, False, False
        for r in boosted:
            is_comp = any(isinstance(t, str) and t.startswith("component:")
                          for t in (r.get("tags") or []))
            applied = r["signals"].get("component_boost_applied", 1.0)
            b = base.get(r["id"])
            if is_comp and r.get("status") == "current" and b is not None and r["score"] > b + 1e-9:
                lifted = True
            if not is_comp and b is not None and abs(r["score"] - b) > 1e-9:
                touched_noncomp = True
            if is_comp and r.get("status") != "current" and applied != 1.0:
                boosted_stale = True
        ok = lifted and not touched_noncomp and not boosted_stale
        detail = (f"current lifted={lifted}, non-component untouched={not touched_noncomp}, "
                  f"stale-component boosted={boosted_stale}")
    finally:
        kr._COMPONENT_BOOST = saved
    return ("boost_safety", ok, detail)


LANDMINES = [inv_owner_isolation, inv_status_default, inv_shim_contract,
             inv_signal_sanity, inv_suppression, inv_boost_safety]


def run_landmines() -> list[tuple[str, bool, str]]:
    out = []
    for fn in LANDMINES:
        try:
            out.append(fn())
        except Exception as exc:  # a crashing invariant is itself a failure
            out.append((fn.__name__, False, f"EXCEPTION: {exc}"))
    return out


# ---------------------------------------------------------------------------
# Boost sweep — how far up does the DNS component:current doc move per weight?
# ---------------------------------------------------------------------------
def run_sweep(weights=(1.0, 1.5, 2.0, 3.0, 5.0)) -> list[dict]:
    saved = kr._COMPONENT_BOOST
    rows = []
    try:
        for w in weights:
            kr._COMPONENT_BOOST = w
            recs = [measure_query(s) for s in QUERIES if s["kind"] == "component"]
            for r in recs:
                rows.append({"weight": w, "query": r["query"],
                             "component_current_rank": r["component_current_rank"],
                             "top1_vsim": r["top1_vsim"], "n": r["n"]})
    finally:
        kr._COMPONENT_BOOST = saved
    return rows


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_corpus(c: dict) -> None:
    print("\n=== CORPUS ===")
    print(f"  total={c['total']}  owners={c['owners']}")
    print(f"  statuses={c['statuses']}")
    print(f"  domains={c['domains']}")
    print(f"  word_count={c['word_count']}")
    print(f"  component docs: {len(c['components'])}")
    for comp in c["components"]:
        tags = [t for t in comp["tags"] if t.startswith("component:")]
        print(f"    - {comp['created_by']} | {comp['system']} | {comp['status']} | {tags}")


def print_measurement(recs: list[dict]) -> None:
    print("\n=== SIGNAL MEASUREMENT (status=all; raw signals behind the fused ordinal) ===")
    print(f"  {'kind':<9} {'owner':<15} {'top1_vsim':>9} {'r1-r5 gap':>9} "
          f"{'mean_vsim':>9} {'overlap':>7} {'comp@':>5}  query")
    for r in recs:
        print(f"  {r['kind']:<9} {r['owner']:<15} "
              f"{_p(r['top1_vsim']):>9} {_p(r['rank1_rank5_gap']):>9} "
              f"{_p(r['mean_vsim']):>9} {r['overlap_count']:>7} "
              f"{str(r['component_current_rank']):>5}  {r['query'][:44]}")

    rel = [r["top1_vsim"] for r in recs
           if r["kind"] in ("relevant", "component") and r["top1_vsim"] is not None]
    noise = [r["top1_vsim"] for r in recs
             if r["kind"] == "noise" and r["top1_vsim"] is not None]
    print("\n  --- SNR summary ---")
    if rel:
        print(f"  relevant/component top1_vsim: "
              f"mean={statistics.mean(rel):.4f} min={min(rel):.4f}")
    if noise:
        print(f"  noise-floor       top1_vsim: "
              f"mean={statistics.mean(noise):.4f} max={max(noise):.4f}")
    if rel and noise:
        margin = min(rel) - max(noise)
        print(f"  separation margin (min relevant − max noise): {margin:+.4f}  "
              f"{'✓ separable' if margin > 0 else '✗ OVERLAP — no clean floor'}")


def _p(v):
    return "  —  " if v is None else f"{v:.4f}"


def print_landmines(results: list[tuple[str, bool, str]]) -> bool:
    print("\n=== LANDMINE INVARIANTS ===")
    all_ok = True
    for name, ok, detail in results:
        all_ok &= ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<24} {detail}")
    return all_ok


def print_sweep(rows: list[dict]) -> None:
    print("\n=== COMPONENT-BOOST SWEEP (rank of the component:current doc per weight) ===")
    print(f"  {'weight':>7} {'query':<40} {'comp@rank':>9} {'top1_vsim':>9}")
    for r in rows:
        print(f"  {r['weight']:>7} {r['query'][:40]:<40} "
              f"{str(r['component_current_rank']):>9} {_p(r['top1_vsim']):>9}")


# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--landmines-only", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--json", type=str, default=None, help="write full results to this path")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"Knowledge Signal & Landmine Harness — {ts}")
    print(f"READ_TARGET={os.environ['OPENBRAIN_READ_TARGET']} "
          f"boost={kr._COMPONENT_BOOST} suppression_floor={kr._VECTOR_SUPPRESSION_FLOOR}")

    payload: dict = {"timestamp": ts}

    if not args.landmines_only:
        corpus = discover_corpus()
        print_corpus(corpus)
        measurement = run_measurement()
        print_measurement(measurement)
        payload["corpus"] = corpus
        payload["measurement"] = measurement

    if args.sweep:
        sweep = run_sweep()
        print_sweep(sweep)
        payload["sweep"] = sweep

    landmines = run_landmines()
    all_ok = print_landmines(landmines)
    payload["landmines"] = [{"name": n, "ok": ok, "detail": d} for n, ok, d in landmines]

    if args.json:
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"\nWrote {args.json}")

    print(f"\n{'✓ ALL LANDMINES CLEAR' if all_ok else '✗ LANDMINE TRIPPED'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
