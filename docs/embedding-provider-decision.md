# Embedding Provider: Why OpenRouter, and Why We're Keeping It

**Decision (2026-08-04): keep OpenRouter as the embedding provider. No migration.**

This doc exists because the question — *"wait, are we still using OpenRouter? I thought we killed
that"* — has already come up once and will come up again. Here's the durable answer so we don't
re-investigate it every few months.

## TL;DR

- OpenBrain embeds `text-embedding-3-small` (1536-dim) through **OpenRouter**. This is **by design**,
  set during the OB2/Supabase build — not a leftover, not a leak. (`OPENBRAIN_ARCHITECTURE.md` §4.)
- **Total cost: ~$0.03 since March 2026.** A $10 credit top-up from March is essentially untouched.
- It was never "moved away from." The Supabase migration was the **storage** layer; the embedding
  **provider** is a separate concern and was never changed.
- **Decision: leave it exactly as-is.** At this cost, any change is effort — and re-embed risk —
  chasing pennies.

## Why OpenRouter and not Anthropic? (the recurring question)

**Anthropic has no embeddings API.** Claude does not produce embeddings, full stop. "All-in on
Anthropic" covers chat / classification / vision — and it does: our SafeIngest classifier is Haiku,
PDF vision is Claude — but embeddings *must* come from OpenAI, Voyage, Cohere, or a self-hosted
model. `text-embedding-3-small` is an OpenAI model; OpenRouter is just the account it's billed
through. So this is **not** an OpenAI-vs-Anthropic choice we can win by switching to Anthropic.

## What do we even embed? (why it's 3 cents)

- **Ingest:** each document's content + its heading-chunks, embedded once at save time.
- **Query:** the query text (~10–30 tokens), embedded to run the vector search.

At ~$0.02 per 1M tokens, $0.03 ≈ **1.5M tokens over five months** — roughly the entire ~800-row
vault embedded once (with chunking) plus light query traffic. It's a lightly-used family vault on
one of the cheapest models made. There is no meaningful cost here, and never was.

## "Can OpenRouter bill against my Anthropic subscription?"

No.

- OpenRouter has its own prepaid billing; by default it does not federate to your Anthropic account.
- Its **BYOK** feature *can* route **Anthropic API** calls to your Anthropic account (~5% fee) — but a
  **claude.ai Pro/Max subscription is not API billing** (API is separate pay-as-you-go credits), and
  it's **moot for embeddings anyway** (Anthropic has none to bill).

## If we ever DO want off OpenRouter (not now, and not for cost)

Only two things would justify it: a hard "zero non-Anthropic vendors" policy, or folding embeddings
into the homelab. Paths, cheapest-risk first:

- **A — OpenAI-direct, same model.** Zero re-embed (identical vectors); drop only the OpenRouter
  middleman. Lateral: swaps one vendor key for another, no cost win.
- **B — Voyage** (Anthropic's recommended embedding partner) or **C — self-hosted** (ties into the
  local-inference project, $0/call). Both require **re-embedding the whole vault + re-running the
  OB3.0 retrieval evals** — i.e. re-touching the exact thing 3.0 fixed. Worth it only if
  self-ownership becomes a goal in its own right.
- **Hard gate if we ever migrate:** retrieval eval pass-rate ≥ the OB3.0 baseline; re-embed into a
  shadow column so cutover is a swap and rollback is a swap-back.

## Ecosystem model inventory (for completeness)

| Task | Model | Provider | Status |
|---|---|---|---|
| Embeddings (query + ingest) | `text-embedding-3-small` | OpenRouter | **KEEP** (this doc) |
| SafeIngest classifier | `claude-haiku-4-5` | Anthropic-direct | already Anthropic |
| PDF-vision OCR | Claude vision | Anthropic | already Anthropic |
| Wiki completion (dormant) | `gpt-4o-mini` | OpenAI/OpenRouter | dormant; switch to Haiku only if the wiki is ever revived |
| Eval Judge B | `gpt-4o` | OpenAI | deliberate cross-vendor independence (generator + Judge A are Claude) — keep unless we choose all-Anthropic |

Net: after this decision, the only non-Anthropic touch-points are **embeddings** (forced — no
Anthropic option) and the eval's **independent second judge** (a feature, not an oversight). One
loose, low-priority follow-up if we ever want it: confirm the Haiku/Sonnet model *versions* are
still current.
