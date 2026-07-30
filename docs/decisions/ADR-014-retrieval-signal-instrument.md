# ADR-014: Retrieval Signal Instrument, Component Boost & Suppression Floor

**Status:** Proposed
**Date:** 2026-07-30
**Applies to:** the OB2 `public.knowledge` read path (`api/knowledge_retrieval.py`).
**Supersedes context in:** the "Retrieval Signal-to-Noise Enhancement" spec (Findings A, B; open thread #1).

## Context

Prior observation held that retrieval quality was "low-scoring" and that the ADR-002
length penalty was fighting authoritative long docs. Both were wrong, for the same
root cause: **the score returned to callers is a pure RRF ordinal.**

Reciprocal Rank Fusion combines the two retrievers on *rank position only*, discarding
pgvector's cosine magnitude and Postgres's `ts_rank` before fusing. The result is a fixed
reciprocal per position — rank 1 is always `1/(60+1) = 0.01639` whether the match is
verbatim or unrelated. Reproduced live 2026-07-30:

- Query "ansible playbook…" → rank-1 fused score `0.01639` exactly.
- Query for a resume variant returned an unrelated "Custom GPT setup" note at rank 4,
  `0.01613` — **1.6% separation between a direct hit and noise.**

There was therefore **no instrument** to measure relevance, set a suppression threshold,
or tune any ranking modifier. Everything downstream (the queued post-ingest validation,
the `component:*` boost) was blocked on this.

### Correction to the enhancement spec (Finding B / §2.5)

The spec assumed the ADR-002 length penalty "pushes authoritative *long* docs down."
It does the opposite. `knowledge_retrieval.py` applies `penalty = wc / 30` **only when
`word_count < 30`** — it down-weights short *fragments*; docs at or above 30 words get
multiplier `1.0` (untouched). Long living docs are never penalized. The "boost vs
penalty tension on long docs" the spec designed around **does not exist**, so the boost
needs no counterbalancing — it only has to clear stale content out of the way.

## Decision

### 1. Expose raw per-retriever signals behind the fused ordinal (Finding A + B)

Keep RRF for *ordering* — the fusion strategy is sound. Stop presenting its output as a
quality measure. Every result now carries a `signals` dict (additive; existing keys
unchanged):

| field | meaning |
|---|---|
| `vector_similarity` | `1 − cosine_distance` from pgvector (the real relevance signal) |
| `vector_distance` | raw pgvector `<=>` distance |
| `lexical_score` | raw Postgres `ts_rank` |
| `vector_rank`, `lexical_rank` | position within each retriever (null if absent) |
| `retrievers_hit` | 1 or 2 — how many retrievers surfaced this row |
| `length_penalty_applied` | the ADR-002 multiplier (1.0 = untouched) — makes Finding B observable |
| `component_boost_applied` | the boost multiplier applied (1.0 = none) |
| `rrf_score`, `final_score` | fused pre-penalty, and final |

This is the relevance instrument. `vector_similarity` is comparable across queries; the
fused ordinal is not.

### 2. Component boost — status-gated (open thread #1)

Authoritative current-state living docs (`component:*` tag) get their fused score
multiplied by `_COMPONENT_BOOST` so stale event notes stop out-ranking them.

**The boost is gated on `status == 'current'`.** A superseded/historical row carries the
*same* `component:*` tag; an ungated boost lifts stale versions above the live one — the
exact disease it was meant to cure. This was caught by the signal-harness boost sweep
(2026-07-30): at weight 5× an *ungated* boost put a **superseded** `dns-current-state`
doc at rank 0, above the current one. Status-gating fixes it.

### 3. Vector-similarity suppression floor (Finding A #3)

`_VECTOR_SUPPRESSION_FLOOR` drops candidates whose best `vector_similarity` is below the
floor, rather than returning the least-bad of several poor results. Keyword-only hits
(no vector similarity) are kept — a lexical exact match is still meaningful.

**Empirical finding: there is no safe static floor at current corpus/embedding quality.**
Measured on 800 rows: min top-1 similarity across genuinely relevant queries = **0.2271**
(`what is an autotroph`); max noise-floor similarity = **0.1787**. Separation margin is
**+0.048** — a floor set to catch noise would also nuke legitimate weak hits. So the floor
ships **disabled** (0.0) and is documented as *not yet safely settable*; the honest fix is
better embeddings / chunking (ADR future), not a threshold guess.

## Defaults — ships behavior-neutral

Both knobs are env-overridable and default to a **no-op**, so this change deploys dormant
and reversible (OB2 cutover discipline):

- `OPENBRAIN_COMPONENT_BOOST` — default `1.0` (OFF). **Recommended `2.0`** (see below).
- `OPENBRAIN_VECTOR_SUPPRESSION_FLOOR` — default `0.0` (OFF). Not recommended to enable yet.

## Evidence (signal harness, 800-row live vault, 2026-07-30)

Boost sweep — rank of the `component:dns-current-state` **current** doc (a contested doc
that stale versions outrank at baseline):

| boost | dns current-doc rank | vlan current-doc rank |
|---|---|---|
| 1.0 (off) | 3 | 0 |
| 1.5 | 1 | 0 |
| **2.0** | **0** | 0 |
| 3.0 / 5.0 | 0 | 0 |

`2.0` is the minimum weight that promotes the contested current doc to rank 0. At `2.0`,
**no non-component query gained a component doc** (no over-promotion) and all six landmine
invariants pass. Recommendation: `OPENBRAIN_COMPONENT_BOOST=2.0`.

## Validation — the signal & landmine harness

`scripts/knowledge_signal_harness.py` is the instrument and the guard. It measures signal
across owners/domains and asserts invariants that a tuning change could silently break:

- `owner_isolation` — Annie's queries never return Mike's rows (cross-tenant leak).
- `status_default_current` — omitting `status` returns only `current`.
- `shim_contract` — the OB1 response shims (`document_id`, `source`, `section`, `heading`,
  `content_type`, `owner`) + `signals` all survive.
- `signal_sanity` — every signal bucket is internally coherent.
- `suppression_floor` — a floor trims noise without emptying a strong query.
- `boost_safety` — a boost lifts *current* component docs, leaves non-component rows
  untouched, and **never** boosts superseded/historical component docs.

## Consequences

- The post-ingest validation enhancement can now read `vector_similarity` instead of a
  constant. Re-point it there.
- Chunking (spec Finding C) remains future work and now has a baseline to measure against.
- The boost is a per-row multiplier; if chunking lands, a multi-chunk living doc would
  contribute multiple boosted rows and must be collapsed by `(system, component)` before
  return. Decide when chunking is designed.

## Not done here (deliberately out of scope)

Chunking, schema changes, backfill/migration, write-path changes, and flipping any
persistent env target. Read-path, additive, reversible only.
