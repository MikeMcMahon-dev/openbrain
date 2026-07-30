# ADR-017: Heading-based chunking for precision on long living docs

**Status:** Proposed (design; implementation staged — no DB mutation without sign-off)
**Date:** 2026-07-30
**Relates to:** ADR-014 (signals), ADR-015 (recall), ADR-016 (skim/fetch). Builds on
the retrieval-SNR spec §3, now justified by measured evidence rather than theory.

## Context — the measured prize

With recall (ADR-015) and payload (ADR-016) fixed, one problem remained unaddressed:
**long multi-topic docs dilute their own details in the vector embedding.** The
precision gate (2026-07-30) measured it across 52 sections of 6 big current-state docs:

- a section embeds a mean **+0.217 cosine closer** to its own sub-topic than the whole
  doc does (median +0.223, max +0.365);
- **94% of sections** a chunk beats the whole doc by ≥0.10 similarity.

Live impact, paraphrased detail query *"how do I revive the proxmox host after it
locked up overnight"*: the authoritative 1,238-word crash doc matched at vector **0.428**
while an unrelated 312-word note scored **0.503**, and a generic 50-word "When All Else
Fails" note out-scored the authoritative record on another detail query. The answer is
*in* the doc — buried in one section, diluted to near the noise floor. These are exactly
the current-state operational records the lab is run from, so the dilution is
consequential, not cosmetic.

Chunking is therefore a **precision repair** for the highest-stakes docs, not an
optimization. (Payload is already handled by skim/fetch, so that is no longer the case
for chunking.)

## Decision

### 1. Chunk model
Split a document on markdown headings (`#`..`###`), one chunk per section (heading +
body). Each chunk is a row carrying:

- **`document_id`** — the parent document's id (chunks of one doc share it)
- **`chunk_index`** — order within the document
- **`heading`** — the section heading (also used as a lightweight skim label)
- **`content`** — the clean section text (what gets displayed / fetched)
- **`embedding`** — computed from **`title + "\n" + content`**, NOT bare content, so a
  section beginning "Comp history: RTR quoted…" still embeds as being *about* its parent
  (the title carries the entity). Display text stays clean; only the embedded text is
  prefixed.
- inherited verbatim from the parent write: `domain, environment, system, tags, status,
  source, created_by, ingest_id, valid_from/until, supersedes_id`.

**Edge cases:**
- Preamble before the first heading → its own chunk (`chunk_index` 0).
- Section < ~40 words → merge forward into the next (no fragment embeddings).
- Section over the embedding window → sub-split on `###`, then paragraphs.
- **No headings at all → single chunk = whole doc** (`chunk_index` 0). Short notes
  (Annie's study snippets, event wraps) are unaffected — they become 1 chunk and behave
  exactly as today.

### 2. Supersession — 1→N, and the trigger change
Today `write_knowledge` (ADR-008) retires prior current rows with a **set-based** UPDATE:
`WHERE system=? AND status='current' AND tags @> ARRAY[component]`. That already retires
*N* rows, so chunked supersession extends for free: chunk the new doc into N rows, all
carrying the component tag, and the same UPDATE retires the entire prior chunk set
atomically. `supersedes_id` links to the prior document (its parent id), not per-chunk.

**The one real conflict:** the current `knowledge` duplicate-current trigger enforces
*one current row per `(system, component)`*. Chunking intends *N* current rows per
identity — so on the chunked store that invariant must change to **one current
*document* per `(system, component)`** (uniqueness on the document set, not the row).
Enforced at the write-transaction level (retire-all-then-insert-all in one tx). This is
why chunking targets a **separate table**, not `knowledge` in place.

### 3. Retrieval — fuse chunks, then collapse
RRF fuses on the unique chunk id (unchanged mechanism). After fusion **collapse by
`document_id`**: return the best-ranking chunk per document, with its heading and a
pointer to sibling chunks, so a 6-section doc presents as one result, not six. The
component boost (ADR-014) applies per chunk; collapse runs after the boost so a boosted
living doc still surfaces once, not six times.

### 4. Table strategy — clone, don't mutate (per Mike, 2026-07-30)
Backfill into a **new `knowledge_chunked` table**, a clone of `knowledge`'s schema plus
`document_id / chunk_index / heading` and the per-document current invariant. Serve reads
from it behind a flag (`OPENBRAIN_READ_TARGET=knowledge_chunked` or a dedicated chunk
flag). Live `knowledge` is never touched. **Rollback = flip the flag.** No `source='%'`
scrub games, no in-place risk.

## Staging — and the DB-mutation boundary

| Phase | Work | Mutates DB? |
|---|---|---|
| **A. Pipeline** | `api/chunking.py` (split, title-prefix, edge cases) + tests | no |
| **B. Migration** | `005_knowledge_chunked.sql` DDL (written, **unapplied**) | no (until applied) |
| **C. Backfill script** | `scripts/backfill_chunks.py` — dry-run diff, re-embed plan | no (dry-run only) |
| **D. Retrieval collapse** | collapse-by-document behind flag + tests | no |
| **E. Apply + backfill** | run migration, populate `knowledge_chunked`, re-embed | **YES — sign-off** |
| **F. Eval** | re-run the gate, inverted, against the chunked store | reads only |
| **G. Cutover** | flip read flag; later, dual-write on ingest | **YES — sign-off** |

**This session builds A–D** (no DB mutation). E onward waits for explicit go.

## Acceptance — the gate, inverted
Re-run the same detail queries against the chunked store:
- the right **section** returns at **vector_similarity > 0.6**, beating the generic
  notes that currently out-score the diluted whole doc;
- rank-1→rank-5 separation widens vs the monolithic baseline;
- a rewrite of a chunked `component:*` doc leaves **zero orphaned `current` chunks**
  (supersession integrity);
- no regression on the existing retrieval landmines.

## Constraints (unchanged, restated)
- **Cross-tenant isolation** during backfill is the top-severity failure — scope every
  read/write by `created_by`, test it.
- **IVFFlat retune:** more rows (chunks) per bucket shifts the recall math (ADR-015);
  re-derive `probes` / candidate-pool on the chunked store, or move to HNSW.
- **Cloudflare WAF** blocks command/path tokens on ingest — test chunking with realistic
  technical content.
- **No credentials** in migration or backfill output; scan before commit.
- **Idempotency:** re-running the backfill must not duplicate chunks (key on
  `(document_id, chunk_index)` / deterministic ingest_id per chunk).
