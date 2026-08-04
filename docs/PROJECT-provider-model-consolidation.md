# Project: Provider & Model Consolidation (off OpenRouter)

**Status:** DRAFT — requirements + plan for review. No changes made.
**Opened:** 2026-08-04
**Trigger:** Live OpenRouter usage observed on a key believed disabled. Investigation found
OpenRouter is the **by-design** embedding provider (`OPENBRAIN_ARCHITECTURE.md` §4), not a leak —
every query and every ingest embeds `text-embedding-3-small` through it. Mike has gone all-in on
Anthropic and wants provider/model choices to be intentional, documented, and consolidated.

---

## The constraint that reshapes everything

**Anthropic has no embeddings API.** Claude does not produce embeddings. So this is *not* a
"swap OpenAI → Anthropic" job. The chat/classifier/vision tasks can be Anthropic; **embeddings
cannot.** They must come from OpenAI, Voyage (Anthropic's recommended embedding partner), Cohere,
or a self-hosted model. That fork is the heart of this project.

Second hard fact: **changing the embedding *model* means re-embedding the entire vault** (~800
rows). Vectors from different models are not comparable — a query embedded by model X cannot be
matched against documents embedded by model Y. Only *staying on `text-embedding-3-small`* avoids a
full re-embed. And a re-embed puts the OB3.0 retrieval-quality work at risk of regression, so it
must be re-validated against the OB3.0 evals, not assumed.

---

## Requirements

**Functional**
- **R1** Every provider/model choice is intentional and documented (no surprises like this one).
- **R2** Zero usage on any provider Mike did not choose.
- **R3** Retrieval quality does **not** regress below the OB3.0 baseline. (Hard gate.)
- **R4** Query + ingest keep working throughout (embeddings always available).
- **R5** Cost is understood; the OpenRouter margin is removed unless we consciously keep it.

**Constraints**
- **C1** Anthropic offers no embeddings → embeddings need OpenAI / Voyage / Cohere / self-hosted.
- **C2** Any embedding-model change ⇒ full re-embed + retrieval re-validation (incomparable vectors).
- **C3** Prod is Vercel (serverless). A self-hosted embedder must be a **network-reachable endpoint**
  on Mike's infra, not an in-process model — Vercel can't host the model itself.
- **C4** Reversibility per step (post-mortem lesson): no destructive cutover without a validated rollback.

---

## The embedding-provider fork (the decision)

| Option | Provider / model | Re-embed vault? | Retrieval-regression risk | OpenAI dependency | API $ | Notes |
|---|---|---|---|---|---|---|
| **A** | OpenAI-direct · `text-embedding-3-small` | **No** (same model) | None | Keeps 1 OpenAI key (embeddings only) | low | Cheapest, safest; identical vectors; removes only the OpenRouter middleman. |
| **B** | Voyage AI · `voyage-3(-lite)` | **Yes** (all rows) | Must re-validate vs OB3.0 | None (Anthropic-aligned) | low | The "Anthropic ecosystem" embedding path; new vendor + key; re-embed + eval gate. |
| **C** | Self-hosted · e.g. `bge`/`nomic` on your infra | **Yes** | Must re-validate | None | $0 API (infra cost) | Ties into the local-inference project; but adds a prod→infra network dependency (C3) and uptime burden. |
| **D** | Keep OpenRouter · any model proxied | Depends on model | Depends | None | low + margin | Keeps the abstraction (swap models via one key), but separate prepaid billing and doesn't reach Anthropic. |

**Framing, not a decision:** if the priority is *no risk to the retrieval quality you just built and
zero re-embed*, **A** wins — you keep the exact model and just cut OpenRouter out, at the cost of a
single OpenAI key used only for embeddings. If *zero OpenAI* is a hard line, **B (Voyage)** is the
Anthropic-aligned answer, but you are signing up for a full re-embed and an honest re-run of the
OB3.0 retrieval evals. **C** is the most interesting long-term (free, self-owned, dovetails with
local-inference) but is the most operational work and the only one that adds a runtime dependency
off Vercel. Mike decides; this doc makes the trade explicit.

---

## Answers to the questions raised

**Q: Is there value in keeping OpenRouter and just changing the embedding model?**
Some, situational. OpenRouter's value is a *single key + one integration for many models* — handy if
you want to A/B embedding models (OpenAI vs Voyage vs Cohere) without integrating each vendor. But:
it adds a margin over provider-direct pricing, is a separate prepaid billing surface, and does not
get you onto Anthropic (no Anthropic embeddings). And changing the model triggers a re-embed
regardless of whether OpenRouter is in front of it. So: keep OpenRouter only if you specifically
want the multi-model abstraction; otherwise it's a middleman on the one call it's making.

**Q: Can OpenRouter bill against my existing Anthropic subscription?**
No, with two corrections worth knowing:
- OpenRouter does have **BYOK** (bring-your-own-key): attach your own Anthropic *API* key and calls
  routed with it bill to *your* Anthropic account (OpenRouter adds a small ~5% BYOK fee). So Anthropic
  *chat* usage could be billed to your Anthropic account through OpenRouter — but there's little
  reason to do that vs calling Anthropic directly.
- **A claude.ai subscription (Pro/Max) is not API billing.** API usage — Anthropic-direct *or* via
  OpenRouter BYOK — is separate pay-as-you-go **API credits** on the Anthropic Console. Your chat
  subscription never covers it.
- **And it's moot for embeddings anyway** — Anthropic has no embedding model to bill against. So
  BYOK-to-Anthropic does nothing for the embedding path; it only matters for Claude *chat* calls,
  which we already make Anthropic-direct.

**Q: Is this the right time for an ecosystem-wide model review?**
Yes — done below. Model releases move fast; several choices predate current options.

---

## Model inventory & review (ecosystem-wide)

| Task | Model | Provider today | Verdict |
|---|---|---|---|
| **Embeddings** (query + ingest) | `text-embedding-3-small` | OpenRouter | **DECIDE** — the fork above. |
| **SafeIngest classifier** | `claude-haiku-4-5-20251001` | Anthropic-direct | **KEEP** — already Anthropic; confirm the version is still current. |
| **Wiki completion** (dormant) | `gpt-4o-mini` | OpenAI/OpenRouter | **SWITCH → Claude Haiku** *if* the wiki ever activates; it's dormant (0 rows, decomm review 2027-02), so no action now beyond noting it. |
| **PDF-vision OCR** | Claude vision | Anthropic/OpenRouter | **KEEP** (Claude); pin to Anthropic-direct so it doesn't need OpenRouter. |
| **Eval Judge B** (`test_answer_fidelity.py`) | `gpt-4o` | OpenAI | **DECIDE** — generator + Judge A are already Claude; Judge B is gpt-4o *deliberately* for cross-vendor independence. Keep it (independence is a feature) or go all-Anthropic (simpler, one less vendor, but you lose the independent second opinion). |

Net after the embedding decision: the classifier and vision are already Claude; the only remaining
OpenAI touch-points are **embeddings** (forced — no Anthropic option) and the **eval Judge B**
(optional independence). Everything else is already where "all-in on Anthropic" wants it.

---

## Migration steps (expand/contract, reversible)

1. **Decide** the embedding provider (fork) and the Judge-B question.
2. **Capture the pre-state baseline** (see harness below) — *before* touching anything.
3. **Provision** the chosen provider key(s) in `.env.local` + Vercel; set `EMBEDDING_MODEL`/base-URL
   env if the model/provider changes. Leave `OPENROUTER_*` in place for now (fallback).
4. **[Only if the model changes] Re-embed into a shadow column.** Write new vectors to
   `knowledge.embedding_v2` (and the chunk table), leaving the live `embedding` untouched, so the
   cutover is a column swap and rollback is swapping back. Verify 100% coverage + correct dims.
5. **Deploy** code that reads the new provider/model (and, if re-embedded, the new column).
6. **Validate** against the harness (below). This is the gate — no further steps until it passes.
7. **Cut over**: remove `OPENROUTER_*` from `.env.local` + Vercel; redeploy; confirm OpenRouter
   usage → 0.
8. **Revoke** the OpenRouter key on OpenRouter's dashboard (definitive kill).
9. **Record it**: update `OPENBRAIN_ARCHITECTURE.md` §4 and write an ADR capturing the provider
   decision (the doc currently *enshrines* OpenRouter as the design — that must change with reality).

---

## Testing harness — validate pre/post state

The point (and a direct lesson from the last post-mortem): **assert against the plan, capture the
baseline first, and make the gate a real gate.**

**Pre-state (record before any change):**
- Provider, model, vector dimensions in use; `count(*)` of rows with a non-null embedding (coverage).
- **Retrieval golden set:** a fixed list of ~15 representative queries → record the top-k document
  IDs + scores returned today. This is the "before" snapshot.
- **OB3.0 eval baseline:** run `scripts/test_answer_fidelity.py` (and the retrieval eval) → record the
  pass rate and per-signal metrics. This is the number R3 must not fall below.
- Cost/usage snapshot: current OpenRouter usage + per-op cost estimate.

**Post-state (must pass to cut over):**
- **If SAME model (provider swap only, e.g. Option A):**
  - *Vector parity:* re-embed a handful of known texts via the new provider; assert cosine ≥ ~0.9999
    against the stored vector (same model ⇒ near-identical). If parity fails, the provider isn't
    actually serving the same model — stop.
  - *Golden set identity:* the 15 queries return the **same** top-k (± float noise).
  - *Eval:* pass rate unchanged.
- **If MODEL CHANGED (re-embed, Options B/C/D-with-new-model):**
  - *Coverage:* 100% of rows re-embedded, correct dimensions, no nulls.
  - *Retrieval regression gate (HARD):* re-run the OB3.0 eval harness; **pass rate ≥ baseline.** The
    golden set may return *different* IDs — that's expected; judge quality against the eval, not
    identity. A drop below baseline blocks the cutover. This is the guardrail on the thing OB3.0 fixed.
- **Functional smoke:** a live query + a live ingest return 200 and produce a stored embedding; the
  Haiku classifier still runs (requires `ANTHROPIC_API_KEY` present).
- **Cutover completeness (post-mortem lesson):** grep the repo for any lingering `OPENROUTER_`
  assumption; run `make capability-audit`; confirm nothing references a provider that's been removed
  and no capability lost its caller.
- **Cost/usage:** OpenRouter usage → 0 on its dashboard after step 7; new-provider usage as expected.

---

## Rollback

- `OPENROUTER_*` stays configured (and the key un-revoked) until the post-state gate passes — so any
  failure is a one-line env revert + redeploy, with embeddings unchanged (same model) or the live
  `embedding` column still intact (shadow-column re-embed).
- Revocation (step 8) is the *last* action, only after validation holds for a day of real traffic.

---

## Open decisions for Mike

1. **Embedding provider — A / B / C / D.** The core trade: *zero re-embed + zero retrieval risk*
   (A, keeps one OpenAI key) vs *zero OpenAI* (B/C, buys a re-embed + an eval re-validation).
2. **Eval Judge B:** keep `gpt-4o` for cross-vendor independence, or go all-Anthropic?
3. **Timing/scope:** embedding migration as its own small project now, or bundle with the eval-judge
   change and the (already-scheduled) wiki decomm?
