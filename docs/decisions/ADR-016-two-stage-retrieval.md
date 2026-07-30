# ADR-016: Two-Stage Retrieval — skim previews, fetch full text by id

**Status:** Proposed (Phases 0–2 implemented; Phase 3 = GPT rollout, pending)
**Date:** 2026-07-30
**Relates to:** ADR-014 (signals instrument), ADR-015 (IVFFlat recall). Answers the
token-budget question the retrieval-SNR spec raised, without chunking.

## Context

The retrieval token cost is an **ongoing per-query tax** paid by the consuming LLM
(each ChatGPT Custom GPT, Claude-over-MCP, the tutor) *every* query, and it compounds
across turns within a conversation. Measured on a real DNS query (`n_results=5`):

- **~12,700 tokens per query response.**
- ~50% of it was **duplicated doc text** — `query_payload` returned the full text in
  *both* `context_used` and `results`.
- `signals` were only ~3% (a rounding error, not the problem).

All current usage is agentic (MCP + GPT function-calling); the website is a backstop.
That makes a two-stage "skim then fetch" flow — cheap previews first, full text only for
the notes the agent selects — a clean fit. Cost becomes proportional to what's used.

## Decision

**Phase 0 — de-duplicate `/query`.** Full grounding text stays once in `context_used`
(the tutor packet needs it); `results[].text` becomes a snippet. `results` remains a
structured sidecar (ids, scores, signals, preview). Contract-compatible — the field is
still present and non-empty.

**Phase 1 — skim + fetch endpoints.**
- `/search` → **skim**: per hit `{id, heading, system, domain, status, tags, snippet
  (~40 words), word_count, score, confidence, signals}` — **no full text.**
- `/fetch` (new) → **fetch by id**: `{ids:[...]}` → full note text. **Owner-scoped:**
  `WHERE id = ANY(...) AND created_by = <authenticated owner>`. The owner comes from
  the request context, never the client body — a caller cannot fetch another tenant's
  note by id. Non-UUID / foreign ids are dropped (returns `[]`, never raises); id count
  capped at 20.

**Phase 2 — MCP tools.** Expose `search` and `fetch` to Claude/MCP alongside `query`,
with descriptions that steer skim-then-fetch. `signals` find their true home here — the
evidence an agent skims to choose what to fetch.

**Phase 3 (pending, needs Mike).** Add `openbrain_search` / `openbrain_fetch` to the
Custom GPT Action specs and update the 3 GPTs' instructions (no API for that — manual).

## Results (measured, live vault)

| path | tokens |
|---|---|
| old `/query` (full dump, pre-dedup) | ~12,700 |
| `/query` after Phase 0 de-dup | ~5,700 (**−55%**) |
| skim (`/search`, 5 previews) | ~1,120 |
| fetch (1 full note) | ~1,520 |
| **two-stage total (skim + fetch 1)** | **~2,640 (−79%)** |

Cross-tenant fetch guard, invalid-id drop, and id cap all covered by
`tests/test_two_stage_retrieval.py` (12 tests) + a live integration check.

## Consequences

- `/query` (bundled tutor packet) is unchanged in behavior beyond de-dup; it remains the
  one-shot path for the website backstop and any "give me everything" caller.
- **Chunking is now even less urgent.** With skim→fetch, full-text tokens are paid only
  for the note the agent picks, so a 741-word doc costs nothing until it's the choice.
  Chunking's remaining case narrows to genuine multi-topic *precision*, which the ADR-014
  instrument will flag if it's real.
- Two-stage is a knowledge-path feature; `fetch_knowledge_by_ids` targets
  `public.knowledge`. On a `thoughts` deployment fetch returns `[]` for knowledge ids
  (safe degradation, no wrong data).

## Security note

Fetch-by-id is the cross-tenant boundary — the highest-severity surface in this work.
Owner scoping is enforced in SQL against the authenticated owner, a falsy owner scopes to
nothing (returns `[]`), and a client-supplied `owner` in the body is ignored. Explicitly
tested.
