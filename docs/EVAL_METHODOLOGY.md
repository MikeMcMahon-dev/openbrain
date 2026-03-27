# OpenBrain Eval Methodology

This document describes the two evaluation harnesses used to measure OpenBrain quality,
the reasoning behind each design decision, and how to interpret results.

---

## Harnesses Overview

| Harness | File | Measures | LLM Calls |
|---|---|---|---|
| Retrieval quality | `scripts/test_query_harness.py` | Did the right chunks surface? | None (pure retrieval) |
| Answer fidelity | `scripts/test_answer_fidelity.py` | Was the generated answer faithful? | Generator + 2 judges |

These measure fundamentally different failure modes:
- **Retrieval failures**: wrong chunks returned; correct answer impossible regardless of generator quality.
- **Fidelity failures**: correct chunks retrieved, but the generator hallucinated or ignored them.

Both must pass independently. A system can have 100% retrieval quality and still hallucinate answers.

---

## Harness 1: Retrieval Quality (`test_query_harness.py`)

### What it measures

For each test query, it calls `retrieve_thoughts()` directly and checks whether the expected
keyword or source appears in the top-2 returned chunks. No LLM generation is involved.

### Pass criteria

- 90% of queries must surface the correct source in position 1 or 2.
- Short-doc bias flags: if the top result has <30 words, it is flagged (not failed) as a
  potential bias artifact from short Slack messages or instruction snippets dominating results.

### Why 1000 queries

The harness expands a seed set of ~38 unique queries to ~1000 using systematic variations
(capitalisation, question forms, typos, qualifier words). This stress-tests that retrieval
is robust to natural language variation, not just exact-match lookup.

### Baseline result (2026-03-27)

- **96.9% pass rate at 1000 queries**
- Bias flags: 86/1000 (8.6%) — short docs surfacing but not blocking correct results
- Confidence distribution: high=228, medium=684, low=86, none=2
- RRF fusion (k=60) + length penalty (threshold=30 words) + confidence scoring active

---

## Harness 2: Answer Fidelity (`test_answer_fidelity.py`)

### What it measures

For each of 25 test cases, the harness:

1. Retrieves chunks via `retrieve_thoughts()` (same as production).
2. Generates an answer using `claude-haiku-4-5-20251001` — simulating what the Custom GPT layer does.
3. Scores the answer with two independent judges.

The key metric is **fidelity**: did the generated answer use only information from the retrieved
chunks? Hallucination = making a claim not supported by any chunk.

### Why simulate the generator

`openbrain_query` returns raw retrieval chunks — there is no LLM generation inside OpenBrain.
The GPT/Claude layer does all generation. To evaluate end-to-end quality without running live
Custom GPT sessions, the harness uses Haiku to simulate the generator step. Haiku is fast and
cheap; it is not the production generator (that is GPT-4o or Claude claude-sonnet-4-6), but it faithfully
simulates the "generate from chunks only" behavior.

### Dual-judge design

Two fully independent judges score each answer:

| Judge | Model | API |
|---|---|---|
| Judge A | `claude-sonnet-4-6` | Anthropic (ANTHROPIC_API_KEY in `.env.local`) |
| Judge B | `gpt-4o` | OpenAI (OPENAI_API_KEY from agent-lab `.env`) |

**Why two judges?**

- A single judge has model-specific biases. Claude may be more forgiving of Claude-generated
  answers. GPT-4o may over-penalize on phrasing differences.
- Independent scoring detects systematic bias: if both judges agree, confidence is high.
  If they disagree, there is genuine ambiguity that warrants human review.
- Dual-judge is the standard approach in LLM evaluation literature (see "LLM-as-a-Judge" work
  from LMSYS, 2023).

**Agreement logic:**

- Both judges within 0.15 fidelity AND same hallucination flag → high-confidence result, no review needed.
- Any divergence beyond that threshold → flagged for human review, NOT auto-scored.

**Graceful degradation:**

If `OPENAI_API_KEY` is unavailable (e.g., agent-lab path not accessible), the harness runs
in single-judge mode and marks all results as `single_judge=True`. Results are still useful
but carry less confidence.

### Why these sources are authoritative

For Mike's infrastructure content, the harness permits cross-referencing:

| Source | Why authoritative |
|---|---|
| `hashicorp.com` | Official docs for Vault, Terraform, Packer |
| `redhat.com` | Official docs for RHEL 9, Kickstart, SELinux |
| `docs.ansible.com` | Official Ansible project documentation |
| `canonical.com` | Official Ubuntu/Snap documentation |
| `*.edu` domains | Academic sources |

**NOT permitted**: reddit, stackoverflow, community forums, wikis. These sources are often
outdated, opinionated, or contain incorrect information for specific versions. The goal is
to validate against primary documentation, not community consensus.

For Annie's study content, no external sources are permitted — the vault markdown files are
the sole ground truth. Annie's brain contains study notes, not published curriculum, so there
is no authoritative external source to cross-reference.

### Test case categories

| Category | Count | Owner | Ground truth |
|---|---|---|---|
| `mike_infra` | 10 | `mike.mcmahon67` | vault + HashiCorp/RedHat docs |
| `annie_study` | 10 | `anneliesepaige` | vault only |
| `adversarial` | 5 | both | hallucination EXPECTED — judges should detect it |

**Adversarial cases** ask questions where the answer is definitively NOT in the brain
(Annie's GPA, ProxMox IP address, Kubernetes cluster details, Ansible version, Beth's birthday).
A good fidelity judge should detect hallucination when the generator makes up an answer for
these questions. Adversarial detection rate is reported separately.

### Escalation rules

- If the non-adversarial disagreement rate exceeds **30%**, the harness warns and stops
  auto-scoring. This indicates the judge prompts need alignment tuning, not a data problem.
- Prompt tuning is capped at **two iterations**. If disagreement persists after two rounds,
  document it and proceed with a PR noting the issue for human review.

### Interpreting disagreement flags

A disagreement flag means:
1. The two judges reached different conclusions about whether the answer was faithful.
2. This is NOT necessarily an error — it may indicate genuine ambiguity in the answer.
3. Human review should look at: was the answer's claim traceable to a specific chunk? If yes,
   Judge A was likely right. If no, Judge B was likely right.

Common disagreement patterns:
- **Paraphrase vs. hallucination**: one judge considers loose paraphrase faithful; the other
  requires verbatim support. Verdict: paraphrase is acceptable if the meaning is preserved.
- **Omission vs. fabrication**: answer omits details from chunks but adds nothing false.
  Verdict: low fidelity score, but hallucination=false.
- **Adversarial with empty chunks**: generator correctly says "I don't know" — some judges
  score this as high fidelity, others as low confidence. Verdict: "I don't know" is correct
  behavior and should not be penalized.

---

## Baseline Scores

### Retrieval quality baseline (2026-03-27, n=1000)

- Pass rate: **96.9%**
- Target: 90%
- Status: CERTIFIED TRUSTWORTHY

### Answer fidelity baseline (2026-03-26, n=25)

- Run pending — baseline result will be recorded in `scripts/answer_fidelity_results.md`
  and `scripts/eval_history.md` after the first full harness execution.
- Expected baseline: avg fidelity >0.70, hallucination detection on all 5 adversarial cases.

---

## Running the Harnesses

```bash
# Retrieval quality — all 1000 cases
.venv/bin/python scripts/test_query_harness.py

# Retrieval quality — first 100, verbose
.venv/bin/python scripts/test_query_harness.py --n 100 --verbose

# Answer fidelity — all 25 cases
.venv/bin/python scripts/test_answer_fidelity.py

# Answer fidelity — adversarial only
.venv/bin/python scripts/test_answer_fidelity.py --adversarial

# Answer fidelity — Annie's content only
.venv/bin/python scripts/test_answer_fidelity.py --owner anneliesepaige --verbose
```

Results are always written to:
- `scripts/query_test_results.md` (retrieval harness)
- `scripts/answer_fidelity_results.md` (fidelity harness)
- `scripts/eval_history.md` (shared cross-run log)

---

## Key Design Constraints

1. **OPENAI_API_KEY is not stored in open-brain's `.env.local`**. It is loaded at runtime from
   `/Users/mmcmahon/src/home-lab/agent-lab/agent_lab/.env`. The agent-lab project owns the key;
   open-brain is a consumer. Rotate the key in agent-lab only.

2. **No vault ingestion required for evaluation**. The harness reads vault markdown files directly
   as ground truth when cross-checking Mike's infrastructure answers. This means eval works even if
   a document was not yet ingested into Supabase.

3. **`retrieve_thoughts()` is called directly** — not through the Vercel HTTP layer. This eliminates
   network latency and auth variability from retrieval measurements.
