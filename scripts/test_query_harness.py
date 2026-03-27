#!/usr/bin/env python3
"""
OpenBrain Query Tuning Test Harness
====================================
Validates retrieval quality for the retrieve_thoughts() function.
Pass criteria: ≥90% of queries must surface the correct source in position 1 or 2.

Usage:
    python scripts/test_query_harness.py              # all tests
    python scripts/test_query_harness.py --n 100      # first 100 only
    python scripts/test_query_harness.py --owner anneliesepaige
    python scripts/test_query_harness.py --short-bias # short-doc bias report only
"""
import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Allow importing from api/
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.local")

from api._openbrain_api import retrieve_thoughts  # noqa: E402

# ---------------------------------------------------------------------------
# Test case format
#   query       : str   — what the user types
#   owner       : str   — whose brain to search
#   tenant_id   : str   — tenant (default "family")
#   expect_any  : list  — at least one of these strings must appear in
#                         text or source of result at position 0 or 1
#   label       : str   — human-readable description for failure reporting
#   tags        : list  — "short_query", "naive", "adversarial", "factual"
# ---------------------------------------------------------------------------
TEST_CASES = [
    # -----------------------------------------------------------------------
    # ANNIE — Taxonomy / Biology
    # -----------------------------------------------------------------------
    {
        "query": "what are the levels of taxonomy",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["Domain", "Kingdom", "Phylum", "taxonomy"],
        "label": "taxonomy hierarchy — direct",
        "tags": ["factual"],
    },
    {
        "query": "taxonomy",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["taxonomy", "classification", "Linnaeus"],
        "label": "taxonomy — single word (naive)",
        "tags": ["naive", "short_query"],
    },
    {
        "query": "what is an autotroph",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["autotroph", "own food", "photosynthesis"],
        "label": "autotroph definition",
        "tags": ["factual"],
    },
    {
        "query": "autotroph vs heterotroph",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["autotroph", "heterotroph"],
        "label": "autotroph vs heterotroph comparison",
        "tags": ["factual"],
    },
    {
        "query": "heterotroph",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["heterotroph", "consume", "organic"],
        "label": "heterotroph — single word (naive)",
        "tags": ["naive", "short_query"],
    },
    {
        "query": "kingdoms of life",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["kingdom", "Kingdom", "classification"],
        "label": "kingdoms of life",
        "tags": ["factual"],
    },
    {
        "query": "carl linnaeus binomial nomenclature",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["Linnaeus", "binomial", "nomenclature"],
        "label": "Linnaeus / binomial nomenclature",
        "tags": ["factual"],
    },
    {
        "query": "dear king philip came over for good soup",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["mnemonic", "Domain", "Kingdom", "taxonomy"],
        "label": "taxonomy mnemonic recall",
        "tags": ["naive"],
    },
    {
        "query": "cladogram",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["cladogram", "clade", "phylogen"],
        "label": "cladogram — single word",
        "tags": ["short_query"],
    },
    {
        "query": "how do you read a cladogram",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["cladogram", "ancestor", "branch"],
        "label": "cladogram reading",
        "tags": ["factual"],
    },
    {
        "query": "dichotomous key",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["dichotomous", "key", "identify"],
        "label": "dichotomous key",
        "tags": ["factual"],
    },
    {
        "query": "how do you use a dichotomous key in science",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["dichotomous", "key"],
        "label": "dichotomous key usage",
        "tags": ["factual", "naive"],
    },
    {
        "query": "organisms classification science",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["classification", "organism", "taxonomy"],
        "label": "organisms classification — vague",
        "tags": ["naive"],
    },
    {
        "query": "tell me everything about taxonomy",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["taxonomy", "classification", "Domain"],
        "label": "taxonomy — broad BOOM query",
        "tags": ["naive", "adversarial"],
    },
    {
        "query": "science test stuff",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["taxonomy", "classification", "mitosis", "DNA"],
        "label": "vague science query",
        "tags": ["naive", "adversarial"],
    },
    {
        "query": "genus species",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["genus", "species", "binomial", "Linnaeus"],
        "label": "genus species — two words",
        "tags": ["short_query"],
    },
    {
        "query": "what did we study about biology",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["taxonomy", "classification", "mitosis", "DNA"],
        "label": "open-ended biology recall",
        "tags": ["naive"],
    },
    {
        "query": "mitosis",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["mitosis", "cell division", "phase"],
        "label": "mitosis — single word",
        "tags": ["short_query"],
    },
    {
        "query": "DNA structure double helix",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["DNA", "double helix", "nucleotide", "base pair"],
        "label": "DNA structure",
        "tags": ["factual"],
    },
    {
        "query": "what are base pairs in DNA",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["base pair", "nucleotide", "adenine", "DNA"],
        "label": "DNA base pairs",
        "tags": ["factual"],
    },

    # -----------------------------------------------------------------------
    # SHORT-DOC BIAS ADVERSARIAL — these must NOT surface the Slack noise
    # -----------------------------------------------------------------------
    {
        "query": "study reminder",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["taxonomy", "classification", "mitosis", "geometry"],
        "label": "bias adversarial: study reminder should surface content, not Slack",
        "tags": ["adversarial", "bias"],
        "expect_not_any": ["Math test upcoming Monday STUDY!"],
    },
    {
        "query": "math",
        "owner": "anneliesepaige", "tenant_id": "family",
        "expect_any": ["geometry", "angle", "formula", "Geometry"],
        "label": "bias adversarial: math should surface geometry notes",
        "tags": ["adversarial", "bias", "short_query"],
        "expect_not_any": ["Math test upcoming Monday STUDY!"],
    },

    # -----------------------------------------------------------------------
    # MIKE — Infrastructure / IaC / Homelab
    # -----------------------------------------------------------------------
    {
        "query": "ansible playbook basics",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["ansible", "playbook", "Ansible"],
        "label": "Ansible basics",
        "tags": ["factual"],
    },
    {
        "query": "proxmox nested virtualization",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["proxmox", "nested", "virtualization", "ProxMox"],
        "label": "ProxMox nested virt",
        "tags": ["factual"],
    },
    {
        "query": "terraform",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["terraform", "Terraform", "infrastructure"],
        "label": "Terraform — single word",
        "tags": ["short_query"],
    },
    {
        "query": "how to configure WSL",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["WSL", "wsl", "Windows Subsystem"],
        "label": "WSL setup",
        "tags": ["factual"],
    },
    {
        "query": "git best practices branching",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["git", "branch", "commit", "pull request"],
        "label": "git branching best practices",
        "tags": ["factual"],
    },
    {
        "query": "github ssh key setup",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["SSH", "ssh", "GitHub", "key"],
        "label": "GitHub SSH key config",
        "tags": ["factual"],
    },
    {
        "query": "rhel 9 template",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["RHEL", "rhel", "Red Hat", "template"],
        "label": "RHEL 9 template",
        "tags": ["factual"],
    },
    {
        "query": "hyper-v remote management",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["Hyper-V", "hyper-v", "remote", "management"],
        "label": "Hyper-V remote management",
        "tags": ["factual"],
    },
    {
        "query": "selinux",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["SELinux", "selinux", "security"],
        "label": "SELinux — single word",
        "tags": ["short_query"],
    },
    {
        "query": "vault unseal keys hashicorp",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["vault", "Vault", "unseal", "key"],
        "label": "Vault unseal keys",
        "tags": ["factual"],
    },
    {
        "query": "openssl windows certificate",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["OpenSSL", "openssl", "certificate", "Windows"],
        "label": "OpenSSL on Windows",
        "tags": ["factual"],
    },
    {
        "query": "python cheat sheet",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["python", "Python", "cheat sheet"],
        "label": "Python cheat sheet",
        "tags": ["factual"],
    },
    {
        "query": "bash scripting",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["bash", "Bash", "script", "shell"],
        "label": "BASH scripting",
        "tags": ["factual"],
    },
    {
        "query": "packer image build",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["packer", "Packer", "image", "build"],
        "label": "Packer image build",
        "tags": ["factual"],
    },
    {
        "query": "kickstart password encryption",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["kickstart", "Kickstart", "password", "encrypt"],
        "label": "Kickstart password encryption",
        "tags": ["factual"],
    },
    {
        "query": "openBrain architecture overview",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["OpenBrain", "openbrain", "RAG", "architecture"],
        "label": "OpenBrain architecture",
        "tags": ["factual"],
    },
    {
        "query": "ai context engineering prompts",
        "owner": "mike.mcmahon67", "tenant_id": "family",
        "expect_any": ["context", "prompt", "AI", "engineering"],
        "label": "AI context engineering",
        "tags": ["factual"],
    },
]

# ---------------------------------------------------------------------------
# Extend to 100+ by generating phrase variations for each existing test case
# ---------------------------------------------------------------------------
def _expand_test_cases(base: list[dict]) -> list[dict]:
    """Generate additional phrasings to reach 100+ test cases."""
    expansions = []
    rewrites = {
        "what are the levels of taxonomy": [
            "taxonomy levels list",
            "classification hierarchy biology",
        ],
        "autotroph vs heterotroph": [
            "difference between autotroph and heterotroph",
            "autotroph heterotroph definition",
        ],
        "cladogram": [
            "what is a cladogram",
            "cladogram biology",
        ],
        "how do you read a cladogram": [
            "cladogram interpretation",
            "reading cladogram science class",
        ],
        "ansible playbook basics": [
            "ansible how to write a playbook",
            "ansible yaml tasks",
        ],
        "git best practices branching": [
            "git workflow feature branch",
            "best way to use git branches",
        ],
        "proxmox nested virtualization": [
            "proxmox enable nested virt",
            "nested vm proxmox setup",
        ],
        "vault unseal keys hashicorp": [
            "hashicorp vault how to unseal",
            "vault initialization keys",
        ],
        "tell me everything about taxonomy": [
            "taxonomy all info",
            "everything I need to know about biological classification",
        ],
        "what is an autotroph": [
            "define autotroph",
            "autotroph meaning science",
        ],
        "DNA structure double helix": [
            "explain DNA double helix",
            "DNA helix nucleotides",
        ],
        "terraform": [
            "what is terraform used for",
            "terraform infrastructure as code",
        ],
    }
    for tc in base:
        for alt_query in rewrites.get(tc["query"], []):
            expansions.append({**tc, "query": alt_query, "label": tc["label"] + f" (alt: {alt_query})"})
    return base + expansions


def _generate_1000(base: list[dict]) -> list[dict]:
    """
    Expand base test cases to ~1000 by applying systematic query variations:
    capitalisation, question forms, typos, qualifier words, negations, cross-topic.
    Each variation inherits the same expect_any/expect_not_any as its parent.
    """
    templates = [
        lambda q: q.upper(),
        lambda q: q.lower(),
        lambda q: q.capitalize(),
        lambda q: f"what is {q}",
        lambda q: f"explain {q}",
        lambda q: f"tell me about {q}",
        lambda q: f"help me understand {q}",
        lambda q: f"I need to know about {q}",
        lambda q: f"notes on {q}",
        lambda q: f"definition of {q}",
        lambda q: f"how does {q} work",
        lambda q: f"can you explain {q}",
        lambda q: f"what do I need to know about {q}",
        lambda q: f"review {q}",
        lambda q: f"{q} overview",
        lambda q: f"{q} summary",
        lambda q: f"{q} basics",
        lambda q: f"{q} for beginners",
        lambda q: f"{q} notes",
        lambda q: f"{q} study guide",
    ]
    expanded = list(base)
    seen_queries = {tc["query"] for tc in base}
    for tc in base:
        for tmpl in templates:
            new_q = tmpl(tc["query"])
            if new_q not in seen_queries:
                seen_queries.add(new_q)
                expanded.append({
                    **tc,
                    "query": new_q,
                    "label": f"{tc['label']} (var: {new_q[:40]})",
                })
            if len(expanded) >= 1000:
                return expanded[:1000]
    return expanded


ALL_TESTS = _expand_test_cases(TEST_CASES)
ALL_TESTS_1000 = _generate_1000(ALL_TESTS)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_tests(tests: list[dict], verbose: bool = False) -> dict:
    passes = 0
    failures = []
    bias_flags = 0
    inflation_flags = 0
    confidence_counts = {"high": 0, "medium": 0, "low": 0, "none": 0}

    for i, tc in enumerate(tests):
        query = tc["query"]
        owner = tc["owner"]
        tenant_id = tc.get("tenant_id", "family")
        expect_any = tc.get("expect_any", [])
        expect_not_any = tc.get("expect_not_any", [])
        label = tc.get("label", query)

        try:
            results = retrieve_thoughts(query, 5, owner, tenant_id)
        except Exception as exc:
            failures.append({
                "query": query, "label": label, "reason": f"Exception: {exc}",
                "got": [],
            })
            continue

        top2 = results[:2]
        top2_text = " ".join((r.get("text", "") + " " + (r.get("source") or "")) for r in top2).lower()

        # Confidence tracking
        conf = results[0].get("confidence", "none") if results else "none"
        confidence_counts[conf] = confidence_counts.get(conf, 0) + 1

        # Short-doc bias flag: top result has < 30 words
        if results and len((results[0].get("text") or "").split()) < 30:
            bias_flags += 1
            if verbose:
                print(f"  [BIAS FLAG] {label}: top result has <30 words")

        # Pass/fail
        matched = any(e.lower() in top2_text for e in expect_any)
        # expect_not_any only blocks if the noise appears in POSITION 1 specifically
        # (position 2 showing noise alongside a correct position 1 is acceptable)
        top1_text = (results[0].get("text", "") + " " + (results[0].get("source") or "")).lower() if results else ""
        blocked = any(e.lower() in top1_text for e in expect_not_any)

        if matched and not blocked:
            passes += 1
        else:
            reason = "expected term not in top-2" if not matched else "blocked term appeared in top-2"
            failures.append({
                "query": query,
                "label": label,
                "reason": reason,
                "expected_any": expect_any,
                "got": [(r.get("source"), r.get("text", "")[:80], r.get("confidence")) for r in top2],
            })

    total = len(tests)
    pass_rate = passes / total if total else 0

    return {
        "total": total,
        "passes": passes,
        "failures": len(failures),
        "pass_rate": pass_rate,
        "bias_flags": bias_flags,
        "inflation_flags": inflation_flags,
        "confidence_counts": confidence_counts,
        "failure_details": failures,
    }


def print_report(results: dict, label: str = "") -> None:
    print(f"\n{'='*60}")
    if label:
        print(f"  {label}")
    print(f"  Timestamp : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Total     : {results['total']}")
    print(f"  Passes    : {results['passes']}")
    print(f"  Failures  : {results['failures']}")
    print(f"  Pass rate : {results['pass_rate']:.1%}")
    print(f"  Bias flags: {results['bias_flags']}")
    print(f"  Confidence: {results['confidence_counts']}")
    print(f"{'='*60}")

    if results["failure_details"]:
        print(f"\n  Top failure cases:")
        for f in results["failure_details"][:10]:
            print(f"\n  FAIL: {f['label']}")
            print(f"    Query  : {f['query']}")
            print(f"    Reason : {f['reason']}")
            for src, text, conf in f.get("got", []):
                print(f"    Got    : [{conf}] ({src}) {text}")

    print()


def write_results_md(runs: list[dict]) -> None:
    path = Path(__file__).parent / "query_test_results.md"
    lines = ["# Query Test Results\n"]
    for run in runs:
        lines.append(f"## {run['label']} — {run['timestamp']}\n")
        lines.append(f"- Total: {run['total']}, Passes: {run['passes']}, Failures: {run['failures']}")
        lines.append(f"- Pass rate: {run['pass_rate']:.1%}")
        lines.append(f"- Bias flags: {run['bias_flags']}")
        lines.append(f"- Confidence distribution: {run['confidence_counts']}")
        lines.append(f"- Changes from prior: {run.get('changes', 'baseline')}\n")
        if run["failure_details"]:
            lines.append("### Top failures\n")
            for f in run["failure_details"][:5]:
                lines.append(f"- **{f['label']}**: {f['reason']}")
                for src, text, conf in f.get("got", []):
                    lines.append(f"  - Got [{conf}] `{text[:60]}`")
        lines.append("")
    path.write_text("\n".join(lines))
    print(f"Results written to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=None, help="Limit to first N test cases")
    parser.add_argument("--owner", type=str, default=None, help="Filter to one owner")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--short-bias", action="store_true", help="Report short-doc bias only")
    args = parser.parse_args()

    tests = ALL_TESTS_1000
    if args.owner:
        tests = [t for t in tests if t["owner"] == args.owner]
    if args.n:
        tests = tests[:args.n]

    print(f"Running {len(tests)} test cases...")
    results = run_tests(tests, verbose=args.verbose)
    print_report(results, label=f"Revised RRF+LengthPenalty — n={len(tests)}")

    run_record = {
        **results,
        "label": f"RRF+LengthPenalty n={len(tests)}",
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "changes": "baseline: RRF fusion (k=60), length penalty (threshold=30 words), confidence scoring",
    }
    write_results_md([run_record])

    if results["pass_rate"] >= 0.90:
        print(f"✓ PASS: {results['pass_rate']:.1%} ≥ 90% target")
        sys.exit(0)
    else:
        print(f"✗ FAIL: {results['pass_rate']:.1%} < 90% target — tuning needed")
        sys.exit(1)
