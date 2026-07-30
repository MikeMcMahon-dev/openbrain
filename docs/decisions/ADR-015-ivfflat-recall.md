# ADR-015: IVFFlat Recall — probes + candidate-pool floor

**Status:** Proposed
**Date:** 2026-07-30
**Applies to:** the OB2 `public.knowledge` vector retrieval path (`api/knowledge_retrieval.py`).
**Relates to:** ADR-014 (the instrument that made this measurable); reframes the "vector is blind to long docs → chunk" thesis in the retrieval-SNR spec.

## Context

With the ADR-014 instrument live, a review query —
`DNS infrastructure Technitium CoreDNS Pi-hole authoritative zones DHCP IPAM` —
showed the authoritative `component:dns-current-state` living doc winning **only on
its lexical hit** (`vector_rank: null`, `retrievers_hit: 1`). The proposed reading
was that long multi-topic docs produce diluted embeddings and are invisible to
vector search, making chunking a *repair*.

Measurement refuted that. The DNS doc's **true** vector similarity to the query is
**0.6076 — rank 1 of 654 current docs.** It is one of the strongest semantic matches
in the store. It was not dilution-blind; it was **under-recalled by the approximate
index.**

Two chained causes:

1. **`ivfflat.probes = 1`** (pgvector's default). The index is
   `ivfflat (embedding vector_cosine_ops) WITH (lists = 100)` over 800 rows — ~8 rows
   per list. probes=1 scans a **single** list (~8 of 800 vectors), so a top-similarity
   doc in another list is never considered.
2. **Candidate under-fetch.** `retrieve_knowledge` pulled `n_results * 2 = 12`
   candidates, below the recall knee.

Evidence — DNS doc's vector rank vs. the two levers:

| lever | value | DNS doc vector rank |
|---|---|---|
| probes (at LIMIT 12) | 1 (default) | **not found** |
| probes | 5 / 10 / 20 | 1 |
| candidate LIMIT (at probes=1) | 12 / 20 | not found |
| candidate LIMIT | 30 / 50 / 120 | 2 |

## Decision

Raise recall on the vector path, both levers, env-overridable:

- **`OPENBRAIN_IVFFLAT_PROBES`** — default **10** (`≈ sqrt(lists)`), applied per query
  via `SET LOCAL ivfflat.probes` (transaction-scoped → safe under the 6543 transaction
  pooler). At 800 rows the extra scan cost is negligible.
- **`OPENBRAIN_VECTOR_POOL_FLOOR`** — default **50**. Fuse from at least this many
  candidates per retriever (capped at `MAX_RESULTS`) instead of `n_results*2`.

These are a **bug fix**, so they default ON (unlike the ADR-014 tuning knobs), but stay
env-overridable for rollback.

## Results

- The DNS current-state doc now wins Chat's falsifiable acceptance test **on merit**:
  position #0, `retrievers_hit = 2`, `vector_similarity = 0.6076 > 0.5494`,
  **`component_boost_applied = 1.0` (boost OFF)** — no chunking required.
- **Fusion overlap across the 39-query correlation set rose 64% → 95%.** RRF now
  genuinely fuses two overlapping lists instead of interleaving two near-disjoint ones.
- All ADR-014 landmines still pass; a new `vector_recall` invariant guards against
  probes silently reverting to 1.

## Consequences for the boost and for chunking

- **The `component:*` boost is vindicated but demoted** from a mask to genuine
  belt-and-suspenders. With recall fixed the authoritative doc wins unaided; the boost
  now only guarantees it against ties.
- **Chunking drops from "repair" to "optional optimization."** The retrieval-efficiency
  problem (finding the right doc) was ANN recall, now fixed. Chunking's remaining,
  independent justification is **token budget** (returning a focused section instead of
  a 741-word doc into every tutor/GPT context); any multi-topic precision gain is
  unproven and must be measured against this post-fix baseline before committing to the
  cost of backfill + `component:*` supersession-across-chunks.

## Note — index choice at this scale

At 800 rows, IVFFlat buys almost nothing: an exact scan is instant and 100%-recall.
`probes=1` traded recall for a speed win that is irrelevant here. Longer term, consider
dropping the ivfflat index (exact search) or rebuilding as HNSW; deferred as a schema
change, out of scope for this code-level fix.
