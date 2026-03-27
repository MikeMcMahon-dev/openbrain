# Query Test Results

## RRF+LengthPenalty n=1000 — 2026-03-27 05:21 UTC

- Total: 1000, Passes: 969, Failures: 31
- Pass rate: 96.9%
- Bias flags: 86
- Confidence distribution: {'high': 228, 'medium': 684, 'low': 86, 'none': 2}
- Changes from prior: baseline: RRF fusion (k=60), length penalty (threshold=30 words), confidence scoring

### Top failures

- **vague science query**: expected term not in top-2
  - Got [low] `Instruction: Always save all test results, study session res`
  - Got [low] `Math test upcoming Monday STUDY!`
- **bias adversarial: study reminder should surface content, not Slack**: expected term not in top-2
  - Got [low] `Instruction: Always save all test results, study session res`
  - Got [low] `Math test upcoming Monday STUDY!`
- **heterotroph — single word (naive) (var: how does heterotroph work)**: expected term not in top-2
  - Got [medium] `Dichotomous Key Notes - Key (1).pdf: Notes about how to use `
  - Got [low] `Sleep over at nanny's Tuesday `
- **vague science query (var: SCIENCE TEST STUFF)**: expected term not in top-2
  - Got [low] `Instruction: Always save all test results, study session res`
  - Got [low] `Math test upcoming Monday STUDY!`
- **vague science query (var: I need to know about science test stuff)**: expected term not in top-2
  - Got [low] `Instruction: Always save all test results, study session res`
  - Got [low] `Math test upcoming Monday STUDY!`
