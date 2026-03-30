# Query Test Results

## RRF+LengthPenalty n=100 — 2026-03-27 21:26 UTC

- Total: 100, Passes: 98, Failures: 2
- Pass rate: 98.0%
- Bias flags: 8
- Confidence distribution: {'high': 22, 'medium': 70, 'low': 8, 'none': 0}
- Changes from prior: baseline: RRF fusion (k=60), length penalty (threshold=30 words), confidence scoring

### Top failures

- **vague science query**: expected term not in top-2
  - Got [low] `Instruction: Always save all test results, study session res`
  - Got [low] `Math test upcoming Monday STUDY!`
- **bias adversarial: study reminder should surface content, not Slack**: expected term not in top-2
  - Got [low] `Instruction: Always save all test results, study session res`
  - Got [low] `Math test upcoming Monday STUDY!`
