# Eval History

Shared log for all OpenBrain evaluation harnesses.

| Run | Timestamp | Harness | n | Score | Details |
|---|---|---|---|---|---|
| 1 | 2026-03-27 05:21 UTC | query_harness | 1000 | 96.9% pass rate | Baseline: RRF+LengthPenalty, confidence scoring |
| 2 | 2026-03-27 (session) | answer_fidelity | 25 | 0.950 avg fidelity | Baseline: dual-judge (claude-sonnet-4-6 + gpt-4o), 12% disagreement, 0/5 hallucinations (correct) |

## 2026-03-27 21:26 UTC | query_harness | n=100
- Pass rate / avg fidelity: 98.0%
- Model versions: generator=N/A (retrieval only), judge_a=N/A, judge_b=N/A
- Disagreements: N/A
- Flags: bias_flags=8, failures=2
