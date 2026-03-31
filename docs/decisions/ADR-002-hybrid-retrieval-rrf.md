# ADR-002: Hybrid Retrieval with RRF + Length Penalty

**Status:** Accepted
**Date:** 2026-03-27

## Context
Pure vector search had retrieval quality issues: short documents scored artificially high due to dense embedding in small space, and keyword-exact matches were being missed. Pass rate on 1000-query harness was below 90% threshold.

## Decision
Hybrid retrieval combining keyword search and vector search, fused via Reciprocal Rank Fusion (RRF) with a length penalty applied to short documents. Confidence scoring added: high/medium/low based on result quality.

## Consequences
- Baseline: 96.9% pass rate at 1000 queries (established 2026-03-27)
- Confidence distribution: high 22%, medium 70%, low 8%
- Short-doc bias eliminated via length penalty threshold
- Weekly eval required to detect regression as corpus grows: run `scripts/test_query_harness.py`
- If pass rate drops below 90%, revisit RRF k value or length penalty threshold before data cleanup
- Keyword results inserted FIRST in merged list; vector fills gaps — watch for regressions if this ordering changes
