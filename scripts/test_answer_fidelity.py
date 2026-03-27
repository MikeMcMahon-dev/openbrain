#!/usr/bin/env python3
"""
OpenBrain Answer Fidelity Evaluation Harness
=============================================
VERIFIED ASSUMPTION: openbrain_query returns raw retrieval chunks — NO LLM generation
inside OpenBrain. The `tutor_prompt` field is a suggested opening line only. The
GPT/Claude layer does all answer generation. This harness simulates that layer,
generates an answer, and scores it with two independent judges.

Eval flow per test case:
  1. Call retrieve_thoughts() directly to get chunks
  2. Use claude-haiku-4-5-20251001 (Anthropic) to generate an answer from chunks
  3. Judge A: claude-sonnet-4-6 (Anthropic) scores the answer
  4. Judge B: gpt-4o (OpenAI) scores the answer
  5. Agreement logic: both judges within 0.15 fidelity + same hallucination flag = high-confidence
     Disagreement = flag for human review

OPENAI_API_KEY is intentionally NOT stored in open-brain's .env.local.
It is loaded at runtime from: /Users/mmcmahon/src/home-lab/agent-lab/agent_lab/.env
Rotate it there. This harness is a consumer, not an owner of that key.

Usage:
    python scripts/test_answer_fidelity.py              # all cases
    python scripts/test_answer_fidelity.py --n 10       # first N
    python scripts/test_answer_fidelity.py --owner anneliesepaige
    python scripts/test_answer_fidelity.py --adversarial   # adversarial only
    python scripts/test_answer_fidelity.py --verbose        # show full judge reasoning
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Env loading — MUST happen before importing project modules
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).parent.parent / ".env.local")

# Load OPENAI_API_KEY from agent-lab (it is not stored here)
_AGENT_LAB_ENV = Path("/Users/mmcmahon/src/home-lab/agent-lab/agent_lab/.env")
_OPENAI_KEY_LOADED = False
if _AGENT_LAB_ENV.exists():
    load_dotenv(_AGENT_LAB_ENV, override=False)  # don't overwrite already-set vars
    _OPENAI_KEY_LOADED = bool(os.getenv("OPENAI_API_KEY"))

from api._openbrain_api import retrieve_thoughts  # noqa: E402

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------
GENERATOR_MODEL = "claude-haiku-4-5-20251001"
JUDGE_A_MODEL = "claude-sonnet-4-6"
JUDGE_B_MODEL = "gpt-4o"

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SINGLE_JUDGE_MODE = not bool(OPENAI_API_KEY)
if SINGLE_JUDGE_MODE:
    print(
        "[WARNING] OPENAI_API_KEY not available. Running in single-judge mode (Judge A only). "
        f"Expected key at: {_AGENT_LAB_ENV}"
    )

# ---------------------------------------------------------------------------
# Judge prompt template
# ---------------------------------------------------------------------------
JUDGE_PROMPT_TEMPLATE = """You are an answer fidelity judge for a RAG system.

QUESTION: {question}

RETRIEVED CHUNKS:
{chunks}

GENERATED ANSWER:
{answer}

Score this answer:
1. fidelity_score (0.0–1.0): How faithfully does the answer use ONLY information from the retrieved chunks? Penalize heavily for claims not found in the chunks.
2. hallucination_detected (true/false): Does the answer contain specific claims, facts, or details NOT present in the retrieved chunks?
3. confidence (high/medium/low): Your confidence in this scoring.
4. reasoning: One sentence explaining your score.

Respond in JSON only. No other text."""

GENERATOR_PROMPT_TEMPLATE = """You are a helpful assistant answering a question based ONLY on the provided context.
Do not add information from your training data. If the context doesn't contain enough information to answer, say so.

CONTEXT:
{chunks}

QUESTION: {question}

Answer based only on the context above:"""

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------
TEST_CASES = [
    # -----------------------------------------------------------------------
    # MIKE — Infrastructure content (~10 cases)
    # -----------------------------------------------------------------------
    {
        "question": "what are the steps to unseal HashiCorp Vault",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": ["hashicorp.com"],
    },
    {
        "question": "how do I configure nested virtualization in ProxMox",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": [],
    },
    {
        "question": "what is the git branching strategy in these notes",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": [],
    },
    {
        "question": "how do I set up a GitHub SSH key",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": [],
    },
    {
        "question": "what does a kickstart file do for password encryption",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": ["redhat.com"],
    },
    {
        "question": "how do I create a RHEL 9 template",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": ["redhat.com"],
    },
    {
        "question": "what is Packer used for in this homelab",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": ["hashicorp.com"],
    },
    {
        "question": "what are the WSL setup steps",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": [],
    },
    {
        "question": "how does Terraform work with WSL",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": ["hashicorp.com"],
    },
    {
        "question": "what Ansible concepts are covered in the notes",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "mike_infra",
        "authoritative_sources": ["docs.ansible.com"],
    },

    # -----------------------------------------------------------------------
    # ANNIE — Study content (~10 cases, vault only)
    # -----------------------------------------------------------------------
    {
        "question": "what are the levels of biological taxonomy in order",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "what is the difference between autotroph and heterotroph",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "explain what a cladogram shows",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "how do you use a dichotomous key",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "what is binomial nomenclature and who invented it",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "what are the kingdoms of living things",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "what did Annie get right in her taxonomy study session",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "what did Annie struggle with in her study session",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "what is the mnemonic for taxonomy order",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },
    {
        "question": "what is DNA and what does the double helix look like",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "annie_study",
        "authoritative_sources": [],
    },

    # -----------------------------------------------------------------------
    # Adversarial hallucination cases (~5 cases designed to induce hallucination)
    # -----------------------------------------------------------------------
    {
        "question": "what is Annie's GPA",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "adversarial",
        "authoritative_sources": [],
        "expect_hallucination": True,
    },
    {
        "question": "what is the IP address of Mike's ProxMox server",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "adversarial",
        "authoritative_sources": [],
        "expect_hallucination": True,
    },
    {
        "question": "what Kubernetes clusters are running in the homelab",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "adversarial",
        "authoritative_sources": [],
        "expect_hallucination": True,
    },
    {
        "question": "what version of Ansible is installed",
        "owner": "mike.mcmahon67",
        "tenant_id": "family",
        "category": "adversarial",
        "authoritative_sources": [],
        "expect_hallucination": True,
    },
    {
        "question": "when is Beth's birthday",
        "owner": "anneliesepaige",
        "tenant_id": "family",
        "category": "adversarial",
        "authoritative_sources": [],
        "expect_hallucination": True,
    },
]


# ---------------------------------------------------------------------------
# HTTP helpers — use stdlib urllib (no extra deps for HTTP)
# ---------------------------------------------------------------------------

def _anthropic_chat(
    model: str,
    system: str,
    user_message: str,
    max_tokens: int = 1024,
) -> str:
    """Call Anthropic messages API via urllib. Returns content text."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not set")

    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user_message}],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["content"][0]["text"]


def _openai_chat(
    model: str,
    system: str,
    user_message: str,
    max_tokens: int = 1024,
) -> str:
    """Call OpenAI chat completions API via urllib. Returns content text."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY not set — single-judge mode active")

    payload = json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


# ---------------------------------------------------------------------------
# Core eval functions
# ---------------------------------------------------------------------------

def format_chunks(chunks: list[dict]) -> str:
    """Format retrieved chunks for inclusion in prompts."""
    if not chunks:
        return "(no chunks retrieved)"
    parts = []
    for i, chunk in enumerate(chunks[:5], 1):
        source = chunk.get("source", "unknown")
        text = chunk.get("text", "").strip()
        parts.append(f"[Chunk {i} | source: {source}]\n{text}")
    return "\n\n".join(parts)


def generate_answer(question: str, chunks: list[dict]) -> str:
    """Step 2: Generate an answer from chunks using claude-haiku."""
    chunks_text = format_chunks(chunks)
    prompt = GENERATOR_PROMPT_TEMPLATE.format(chunks=chunks_text, question=question)
    try:
        return _anthropic_chat(
            model=GENERATOR_MODEL,
            system="You answer questions based strictly on provided context. Do not hallucinate.",
            user_message=prompt,
            max_tokens=512,
        )
    except Exception as exc:
        return f"[GENERATION ERROR: {exc}]"


def parse_judge_response(raw: str) -> dict:
    """Parse JSON from judge response, with fallback."""
    raw = raw.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
    try:
        data = json.loads(raw)
        return {
            "fidelity_score": float(data.get("fidelity_score", 0.0)),
            "hallucination_detected": bool(data.get("hallucination_detected", False)),
            "confidence": str(data.get("confidence", "low")),
            "reasoning": str(data.get("reasoning", "")),
        }
    except Exception:
        return {
            "fidelity_score": 0.0,
            "hallucination_detected": True,
            "confidence": "low",
            "reasoning": f"[PARSE ERROR: {raw[:120]}]",
        }


def run_judge_a(question: str, chunks: list[dict], answer: str) -> dict:
    """Judge A: claude-sonnet-4-6 via Anthropic API."""
    chunks_text = format_chunks(chunks)
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        question=question,
        chunks=chunks_text,
        answer=answer,
    )
    try:
        raw = _anthropic_chat(
            model=JUDGE_A_MODEL,
            system="You are a strict answer fidelity judge. Respond only in valid JSON.",
            user_message=prompt,
            max_tokens=256,
        )
        result = parse_judge_response(raw)
        result["judge"] = "judge_a"
        result["model"] = JUDGE_A_MODEL
        return result
    except Exception as exc:
        return {
            "judge": "judge_a",
            "model": JUDGE_A_MODEL,
            "fidelity_score": 0.0,
            "hallucination_detected": True,
            "confidence": "low",
            "reasoning": f"[JUDGE A ERROR: {exc}]",
            "error": str(exc),
        }


def run_judge_b(question: str, chunks: list[dict], answer: str) -> dict | None:
    """Judge B: gpt-4o via OpenAI API. Returns None if OPENAI_API_KEY unavailable."""
    if SINGLE_JUDGE_MODE:
        return None
    chunks_text = format_chunks(chunks)
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        question=question,
        chunks=chunks_text,
        answer=answer,
    )
    try:
        raw = _openai_chat(
            model=JUDGE_B_MODEL,
            system="You are a strict answer fidelity judge. Respond only in valid JSON.",
            user_message=prompt,
            max_tokens=256,
        )
        result = parse_judge_response(raw)
        result["judge"] = "judge_b"
        result["model"] = JUDGE_B_MODEL
        return result
    except Exception as exc:
        return {
            "judge": "judge_b",
            "model": JUDGE_B_MODEL,
            "fidelity_score": 0.0,
            "hallucination_detected": True,
            "confidence": "low",
            "reasoning": f"[JUDGE B ERROR: {exc}]",
            "error": str(exc),
        }


def judge_agreement(judge_a: dict, judge_b: dict | None) -> dict:
    """
    Compute agreement between judges.
    Returns:
      agreed: bool
      reason: str
      final_fidelity: float
      final_hallucination: bool
      review_flag: bool
    """
    if judge_b is None:
        return {
            "agreed": None,
            "reason": "single-judge mode (no Judge B)",
            "final_fidelity": judge_a["fidelity_score"],
            "final_hallucination": judge_a["hallucination_detected"],
            "review_flag": False,
            "single_judge": True,
        }

    fidelity_diff = abs(judge_a["fidelity_score"] - judge_b["fidelity_score"])
    hallucination_match = judge_a["hallucination_detected"] == judge_b["hallucination_detected"]

    if fidelity_diff <= 0.15 and hallucination_match:
        return {
            "agreed": True,
            "reason": f"fidelity diff={fidelity_diff:.2f}, hallucination={hallucination_match}",
            "final_fidelity": (judge_a["fidelity_score"] + judge_b["fidelity_score"]) / 2,
            "final_hallucination": judge_a["hallucination_detected"],
            "review_flag": False,
            "single_judge": False,
        }
    else:
        disagreement_reason = []
        if fidelity_diff > 0.15:
            disagreement_reason.append(
                f"fidelity diff={fidelity_diff:.2f} (A={judge_a['fidelity_score']:.2f}, B={judge_b['fidelity_score']:.2f})"
            )
        if not hallucination_match:
            disagreement_reason.append(
                f"hallucination mismatch (A={judge_a['hallucination_detected']}, B={judge_b['hallucination_detected']})"
            )
        return {
            "agreed": False,
            "reason": "; ".join(disagreement_reason),
            "final_fidelity": (judge_a["fidelity_score"] + judge_b["fidelity_score"]) / 2,
            "final_hallucination": judge_a["hallucination_detected"] or judge_b["hallucination_detected"],
            "review_flag": True,
            "single_judge": False,
        }


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_eval(tests: list[dict], verbose: bool = False) -> dict:
    results = []
    disagreements = 0
    human_review_items = []
    total_fidelity = 0.0
    hallucination_detected_count = 0
    adversarial_correct = 0
    adversarial_total = 0

    for i, tc in enumerate(tests):
        question = tc["question"]
        owner = tc["owner"]
        tenant_id = tc.get("tenant_id", "family")
        category = tc.get("category", "unknown")
        expect_hallucination = tc.get("expect_hallucination", False)

        print(f"  [{i+1}/{len(tests)}] {category}: {question[:60]}...")

        # Step 1: Retrieve
        try:
            chunks = retrieve_thoughts(question, 5, owner, tenant_id)
        except Exception as exc:
            chunks = []
            print(f"    [RETRIEVAL ERROR: {exc}]")

        # Step 2: Generate
        answer = generate_answer(question, chunks)

        # Step 3: Judge A
        judge_a = run_judge_a(question, chunks, answer)

        # Step 4: Judge B
        judge_b = run_judge_b(question, chunks, answer)

        # Step 5: Agreement
        agreement = judge_agreement(judge_a, judge_b)

        if agreement["review_flag"]:
            disagreements += 1
            human_review_items.append({
                "question": question,
                "owner": owner,
                "category": category,
                "judge_a": judge_a,
                "judge_b": judge_b,
                "agreement": agreement,
            })

        total_fidelity += agreement["final_fidelity"]
        if agreement["final_hallucination"]:
            hallucination_detected_count += 1

        # Adversarial tracking
        if expect_hallucination:
            adversarial_total += 1
            if agreement["final_hallucination"]:
                adversarial_correct += 1  # we WANT hallucination detected here

        record = {
            "question": question,
            "owner": owner,
            "category": category,
            "chunk_count": len(chunks),
            "answer": answer,
            "judge_a": judge_a,
            "judge_b": judge_b,
            "agreement": agreement,
            "expect_hallucination": expect_hallucination,
        }
        results.append(record)

        if verbose:
            print(f"    Fidelity: {agreement['final_fidelity']:.2f} | Hallucination: {agreement['final_hallucination']}")
            print(f"    Judge A: {judge_a['reasoning']}")
            if judge_b:
                print(f"    Judge B: {judge_b['reasoning']}")
            if agreement["review_flag"]:
                print(f"    *** DISAGREEMENT: {agreement['reason']} ***")

    n = len(tests)
    avg_fidelity = total_fidelity / n if n else 0.0
    disagreement_rate = disagreements / n if n else 0.0

    return {
        "total": n,
        "results": results,
        "avg_fidelity": avg_fidelity,
        "hallucination_count": hallucination_detected_count,
        "disagreements": disagreements,
        "disagreement_rate": disagreement_rate,
        "human_review_items": human_review_items,
        "adversarial_correct": adversarial_correct,
        "adversarial_total": adversarial_total,
        "single_judge_mode": SINGLE_JUDGE_MODE,
        "generator_model": GENERATOR_MODEL,
        "judge_a_model": JUDGE_A_MODEL,
        "judge_b_model": JUDGE_B_MODEL if not SINGLE_JUDGE_MODE else "N/A (single-judge)",
    }


def print_report(eval_result: dict, verbose: bool = False) -> None:
    n = eval_result["total"]
    avg_fidelity = eval_result["avg_fidelity"]
    disagreements = eval_result["disagreements"]
    single_judge = eval_result["single_judge_mode"]

    print(f"\n{'='*70}")
    print(f"  OpenBrain Answer Fidelity Eval")
    print(f"  Timestamp : {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Cases     : {n}")
    print(f"  Avg fidelity: {avg_fidelity:.3f}")
    print(f"  Hallucinations detected: {eval_result['hallucination_count']}/{n}")
    print(f"  Judge mode: {'SINGLE (Judge A only)' if single_judge else 'DUAL (A + B)'}")
    print(f"  Generator : {eval_result['generator_model']}")
    print(f"  Judge A   : {eval_result['judge_a_model']}")
    print(f"  Judge B   : {eval_result['judge_b_model']}")
    print(f"  Disagreements: {disagreements} ({eval_result['disagreement_rate']:.1%})")

    if eval_result["adversarial_total"] > 0:
        adv_rate = eval_result["adversarial_correct"] / eval_result["adversarial_total"]
        print(
            f"  Adversarial detection: {eval_result['adversarial_correct']}/{eval_result['adversarial_total']} "
            f"({adv_rate:.1%}) — expected hallucinations correctly flagged"
        )

    if eval_result["human_review_items"]:
        print(f"\n  *** HUMAN REVIEW REQUIRED — {len(eval_result['human_review_items'])} cases ***")
        for item in eval_result["human_review_items"]:
            print(f"\n  DISAGREEMENT: {item['question']}")
            print(f"    Reason: {item['agreement']['reason']}")
            print(f"    Judge A fidelity={item['judge_a']['fidelity_score']:.2f} hallucination={item['judge_a']['hallucination_detected']}")
            if item["judge_b"]:
                print(f"    Judge B fidelity={item['judge_b']['fidelity_score']:.2f} hallucination={item['judge_b']['hallucination_detected']}")

    # Escalation check: >30% disagreement on non-adversarial
    non_adv_results = [r for r in eval_result["results"] if not r.get("expect_hallucination")]
    non_adv_disagreements = sum(
        1 for r in non_adv_results if r["agreement"]["review_flag"]
    )
    non_adv_rate = non_adv_disagreements / len(non_adv_results) if non_adv_results else 0.0
    if non_adv_rate > 0.30:
        print(f"\n  !!! ESCALATION: Non-adversarial disagreement rate={non_adv_rate:.1%} > 30% threshold")
        print("  !!! Investigate judge prompt alignment before proceeding with automated scoring.")

    if verbose:
        print(f"\n  Per-case results:")
        for r in eval_result["results"]:
            flag = " [REVIEW]" if r["agreement"]["review_flag"] else ""
            hall = " [HALLUCINATION]" if r["agreement"]["final_hallucination"] else ""
            adv = " [ADVERSARIAL]" if r.get("expect_hallucination") else ""
            print(f"    [{r['category']}] {r['question'][:55]:<55} fidelity={r['agreement']['final_fidelity']:.2f}{flag}{hall}{adv}")
            if r["agreement"]["review_flag"] or r.get("expect_hallucination"):
                print(f"      Judge A: {r['judge_a']['reasoning']}")
                if r["judge_b"]:
                    print(f"      Judge B: {r['judge_b']['reasoning']}")

    print(f"{'='*70}\n")


def write_results_md(eval_result: dict) -> Path:
    """Write timestamped results to answer_fidelity_results.md."""
    path = Path(__file__).parent / "answer_fidelity_results.md"
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    n = eval_result["total"]
    avg_fidelity = eval_result["avg_fidelity"]
    disagreements = eval_result["disagreements"]
    single_judge = eval_result["single_judge_mode"]

    lines = [f"# Answer Fidelity Results\n"]
    lines.append(f"## Baseline — {timestamp}\n")
    lines.append(f"- Cases: {n}")
    lines.append(f"- Avg fidelity: {avg_fidelity:.3f}")
    lines.append(f"- Hallucinations detected: {eval_result['hallucination_count']}/{n}")
    lines.append(f"- Judge mode: {'SINGLE (Judge A only — no OpenAI key)' if single_judge else 'DUAL (Judge A: claude-sonnet-4-6, Judge B: gpt-4o)'}")
    lines.append(f"- Generator: {eval_result['generator_model']}")
    lines.append(f"- Disagreements: {disagreements} ({eval_result['disagreement_rate']:.1%})")
    if eval_result["adversarial_total"] > 0:
        adv_rate = eval_result["adversarial_correct"] / eval_result["adversarial_total"]
        lines.append(f"- Adversarial detection: {eval_result['adversarial_correct']}/{eval_result['adversarial_total']} ({adv_rate:.1%})\n")
    else:
        lines.append("")

    if eval_result["human_review_items"]:
        lines.append("### Human Review Required\n")
        for item in eval_result["human_review_items"]:
            lines.append(f"- **{item['question']}** ({item['category']})")
            lines.append(f"  - Reason: {item['agreement']['reason']}")
            lines.append(f"  - Judge A: fidelity={item['judge_a']['fidelity_score']:.2f}, hallucination={item['judge_a']['hallucination_detected']}")
            if item["judge_b"]:
                lines.append(f"  - Judge B: fidelity={item['judge_b']['fidelity_score']:.2f}, hallucination={item['judge_b']['hallucination_detected']}")
        lines.append("")

    lines.append("### Per-Case Results\n")
    lines.append("| Category | Question | Fidelity | Hallucination | Agreement | Review |")
    lines.append("|---|---|---|---|---|---|")
    for r in eval_result["results"]:
        q = r["question"][:50]
        fidelity = f"{r['agreement']['final_fidelity']:.2f}"
        hall = "YES" if r["agreement"]["final_hallucination"] else "no"
        agreed = "single" if r["agreement"].get("single_judge") else ("YES" if r["agreement"]["agreed"] else "NO")
        review = "REVIEW" if r["agreement"]["review_flag"] else ""
        adv_marker = " [ADV]" if r.get("expect_hallucination") else ""
        lines.append(f"| {r['category']}{adv_marker} | {q} | {fidelity} | {hall} | {agreed} | {review} |")

    path.write_text("\n".join(lines) + "\n")
    print(f"Results written to {path}")
    return path


def append_eval_history(eval_result: dict) -> None:
    """Append a summary line to scripts/eval_history.md (shared across harnesses)."""
    history_path = Path(__file__).parent / "eval_history.md"
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    n = eval_result["total"]
    avg_fidelity = eval_result["avg_fidelity"]
    disagreements = eval_result["disagreements"]
    single_judge = eval_result["single_judge_mode"]

    judge_b_label = "N/A (single-judge)" if single_judge else eval_result["judge_b_model"]
    review_items = [item["question"] for item in eval_result["human_review_items"]]

    entry_lines = [
        f"\n## {timestamp} | answer_fidelity | n={n}",
        f"- Pass rate / avg fidelity: {avg_fidelity:.3f}",
        f"- Model versions: generator={eval_result['generator_model']}, judge_a={eval_result['judge_a_model']}, judge_b={judge_b_label}",
        f"- Disagreements: {disagreements}",
    ]
    if review_items:
        entry_lines.append(f"- Flags (human review): {'; '.join(review_items[:5])}")
    else:
        entry_lines.append("- Flags: none")

    entry = "\n".join(entry_lines) + "\n"

    if history_path.exists():
        with open(history_path, "a") as f:
            f.write(entry)
    else:
        header = "# Eval History\n\nShared log for all OpenBrain evaluation harnesses.\n"
        history_path.write_text(header + entry)

    print(f"Eval history appended to {history_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="OpenBrain Answer Fidelity Evaluation")
    parser.add_argument("--n", type=int, default=None, help="Limit to first N test cases")
    parser.add_argument("--owner", type=str, default=None, help="Filter to one owner")
    parser.add_argument("--adversarial", action="store_true", help="Run adversarial cases only")
    parser.add_argument("--verbose", action="store_true", help="Show full judge reasoning")
    args = parser.parse_args()

    tests = list(TEST_CASES)

    if args.adversarial:
        tests = [t for t in tests if t.get("category") == "adversarial"]
    elif args.owner:
        tests = [t for t in tests if t["owner"] == args.owner]

    if args.n:
        tests = tests[:args.n]

    print(f"Running {len(tests)} answer fidelity eval cases...")
    if SINGLE_JUDGE_MODE:
        print(f"[SINGLE-JUDGE MODE] Judge B (gpt-4o) unavailable. Results flagged accordingly.")

    eval_result = run_eval(tests, verbose=args.verbose)
    print_report(eval_result, verbose=args.verbose)

    write_results_md(eval_result)
    append_eval_history(eval_result)
