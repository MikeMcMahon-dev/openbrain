# Answer Fidelity Results

---

## Baseline Run — 2026-03-27

- **Cases:** 25 (15 Mike infra, 5 Annie study, 5 adversarial hallucination traps)
- **Avg fidelity score:** 0.950
- **Hallucinations detected:** 0/5 adversarial — correct behavior (generator said "not in vault" for all 5 rather than fabricating)
- **Judge mode:** DUAL (Judge A: claude-sonnet-4-6, Judge B: gpt-4o)
- **Disagreements:** 3 (12% — well under 30% escalation threshold)
- **Generator model:** claude-haiku-4-5-20251001

### Disagreement breakdown

| # | Case | Reason | Resolution |
|---|---|---|---|
| 1 | Mike infra — redirect answer | Judge B overly strict on "redirect to docs" with no chunks | Judge A correct — not a hallucination |
| 2 | Mike infra — redirect answer | Same pattern as above | Judge A correct — not a hallucination |
| 3 | Annie study | Judge B JSON parse error (`confidence: high` unquoted) | False positive — parse_error handling added in post-baseline fix |

### Notes

- Adversarial 0/5 detection is correct: the generator faithfully refused to answer rather than hallucinating, yielding fidelity=1.0 on all 5 cases.
- Judge B parse error (disagreement #3) patched post-baseline: parse errors now treated as single-judge fallback, not real disagreements.
- Judge B "redirect to docs" strictness (disagreements #1-2) is a known Judge B characteristic — not patched, worth monitoring across future runs.
