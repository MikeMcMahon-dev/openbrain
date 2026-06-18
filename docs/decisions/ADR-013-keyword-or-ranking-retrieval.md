# ADR-013: OR-ranked keyword retrieval (loosen the websearch AND bar)

**Status:** Proposed — prototype built and tested on `feat/keyword-or-retrieval-adr`,
pending owner approval before merge/deploy. No production writes or deploys made.

**Date:** 2026-06-18

## Context

Both retrieval paths build their keyword candidate set with
`websearch_to_tsquery('english', <query>)`. `websearch_to_tsquery` **ANDs every
unquoted term**, so a multi-word natural-language query compiles to a conjunction:

```
'failure' & 'detection' & 'dashboard' & 'pushgateway' & 'ocr' & 'cost' & 'model' & 'routing' & 'observability'
```

i.e. "return only documents containing **all** of these." Almost no document clears
that bar, so the keyword arm of the hybrid contributes **zero** rows and the result
set collapses to the vector arm alone (or to nothing, when vector is weak/degraded).

This is not hypothetical. Measured against the live `knowledge` corpus, scoped to one
owner:

| Query | `websearch` (AND) matches |
|---|---|
| `failure detection dashboard Pushgateway OCR cost model routing observability` | **0** |
| `tell me about a time I dropped the ball OB2 stalled cutover` | **0** |
| `OB2 deployment lessons gating reversible rollback` | 1 |

The failure mode is silent: an over-specified query returns the RRF noise floor
(~0.015 scores) that looks like "no good match" rather than "the keyword bar was
impossible." It is the same class of blind spot that let the original `(file, source)`
fusion bug (ADR-011) and the status-only smoke gap hide for weeks — the system
returning *something* plausible while actually matching *nothing*.

## Decision

Replace the keyword query's implicit AND with an **OR-ranked, stemmed** tsquery:

- Tokenize the query into terms; build one `plainto_tsquery('english', <term>)` per
  term (`plainto_tsquery` stems each term so it matches the english-stemmed
  `tsvector`); OR-combine them with the `||` tsquery operator.
- Rank with `ts_rank(to_tsvector(content), <OR-query>)` — documents matching *more*
  (and better) of the query terms rank higher. Partial matches survive instead of
  being eliminated.
- Fall back to the original `websearch_to_tsquery` only when the query yields no
  usable terms (e.g. all stopwords/punctuation).
- Term count is capped (`_MAX_OR_TERMS = 16`) to bound SQL size.

Implemented as a shared helper `api/_openbrain_api._or_tsquery_fragment(query)` used
by both `search_knowledge_keyword_candidates` (the live path) and
`search_keyword_candidates` (the `thoughts` rollback standby), so behaviour stays
identical across a flip/rollback.

The precision the OR gives up is recovered by the existing **RRF fusion + vector**
arm: keyword now supplies broad recall, vector supplies semantic precision, RRF
blends them. Single-term queries are unchanged (one `plainto_tsquery` ≈ the old
`websearch` for one term).

## Test evidence (read-only, live corpus, one owner)

| Query | OLD AND keyword | NEW OR keyword | Correct row at top? |
|---|---|---|---|
| 8-term "failure detection … observability" | 0 | 5 | yes (monitoring content) |
| "dropped the ball OB2 stalled cutover" | 0 | 5 | yes (interview story) |
| "OB2 lessons gating reversible rollback" | 1 | 5 | yes (lessons entry) |

Every query that previously zeroed the keyword arm now returns relevant rows with the
intended entry ranked first.

## Consequences

- **Recall up, no recall cliff.** Multi-term queries return best-partial-match instead
  of nothing. This is the primary win.
- **Keyword precision down slightly**, recovered by `ts_rank` ordering + RRF + vector.
  For a personal/family vault this is the right trade — missing your own note is worse
  than a slightly noisier tail.
- **Pure query change.** No schema change, no migration, no data rewrite. Reversible by
  reverting the two functions. Deploys like any code change (gated on approval).
- **Performance:** an OR of ≤16 `plainto_tsquery` over a GIN-indexed `tsvector` is
  trivial at this corpus size and remains cheap at 10k+ rows.
- Applies to both `knowledge` and `thoughts` keyword candidates for consistency.

## Related finding: live-ingest tags are derived, not honored (tag enrichment)

While verifying retrieval, six freshly-ingested session entries (monitoring + OB2)
landed with thin/derived tags (mostly just `shape:note`; the two interview entries got
`Career`/`Interview` only because "interview" in the topic tripped the career-marker
rule). Root cause: the **legacy ingest path derives tags from subject/topic via
`map_to_taxonomy` and ignores producer-supplied `tags`** — the per-owner honor policy
(ADR-012 amendment) covers `domain`/`environment` but not `tags`. Content retrieval is
unaffected (text + vector), but tag faceting is.

Remediation (gated, owner-approved): `scripts/enrich_session_tags.py` does a
vocabulary-validated `UPDATE` of those six rows' tags (dry-run by default; `--execute`
behind confirmation). A longer-term option — honoring producer `tags` on the ingest
path the way `domain`/`environment` are honored — is noted for a future ADR amendment.

## Rollout

1. Owner approval (this ADR is Proposed).
2. Merge `feat/keyword-or-retrieval-adr` → deploy (auto-deploy via the now-restored
   GitHub→Vercel pipeline).
3. Run `scripts/enrich_session_tags.py --execute` after approval to fix the six rows.
4. Verify: `scripts/smoke_checks.py --live` (the ADR-012-era content check stays green),
   and re-run the A/B queries above against production.
