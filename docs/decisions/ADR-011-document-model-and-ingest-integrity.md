# ADR-011: Document-Model Retrieval Keying & Ingest-Time Field Integrity

**Status:** Proposed
**Date:** 2026-06-17

## Scope update (2026-06-17)

Investigation established that OB2 is **stalled before cutover** — the live system is still
legacy `thoughts`, and `public.knowledge` has **no semantic retrieval** (see
`docs/OB2-CUTOVER-PLAN.md`). Decision: finish the OB2 cutover, with **this ADR as the
retrieval + ingest-integrity spec the cutover consumes**. The decisions below therefore apply
primarily to the **new `knowledge` path** being built (key fusion on `knowledge.id`; server-side
field integrity + `shape` on the `knowledge` ingest). The `thoughts` fusion-key fix is retained
as an **optional standby hotfix** so the rollback target stays healthy during cutover.

## Context

Retrieval QA on a freshly-ingested, all-text vault (Annie's Linux command reference,
owner `anneliesepaige`) surfaced that **every query returned exactly one result**,
regardless of `n_results`, and frequently the *wrong* one. Root-cause investigation
(read-only diagnostics in `ingest/diag_*.py`, run against production Supabase) established:

### 1. RRF fusion collapses on a non-unique key
`retrieve_thoughts` keys Reciprocal Rank Fusion on `(metadata.file, effective_source)`
(`api/_openbrain_api.py:813,818`, via `build_row_payload:524-525`). For `source_type=text`
ingestion, `_write_text_ingest` writes metadata `{source_type, owner, topic, subject}` only —
no `file`, no `source`/`uri`. So **every text row resolves to the identical key
`(None, 'text')`** and merges into a single fusion bucket. The surviving row is the
*last-iterated* keyword candidate (lowest `ts_rank`), and `query_confidence` is inflated by
summing RRF contributions across all collapsed rows. Net: the system returns the *worst*
candidate with falsely *high* confidence.

This is **original scaffolding code** (commit `8e508f5`), not introduced by the OB2
(`knowledge` table) work — OB2 lives on a separate table and endpoints. The bug was masked
historically because diverse content (Slack with channel IDs, files with paths) produced
diverse keys; an all-text vault is the first to expose it.

Measured fusion-key diversity (distinct keys / rows):

| Owner | Rows | Distinct fusion keys (today) | Distinct `document_id` |
|---|---|---|---|
| mike.mcmahon67 | 679 | 87 | 248 |
| anneliesepaige | 68 | 2 | 65 |
| snapple01 (Beth) | 9 | 1 | 0 (all NULL) |

### 2. Ingest places no integrity contract on its fields
All ingestion funnels through one chokepoint, `ingest_payload` (`api/_openbrain_api.py`),
called by every entry point (`api/ingest.py`, `api/chatgpt.py`, `api/claude.py`,
`api/mcp_http.py`, `brain_server/server.py`). It validates `source_type` and non-empty
`source`, but `subject`/`topic` are **optional and silently defaulted** (`derive_subject_topic`:
subject ← filename stem or raw content; topic ← today's UTC date). There is **no field
describing content shape/use**, and no validation that declared values are meaningful. The
per-record tagging in Annie's payload was the authoring client's discretion, not an enforced
contract. Stored tags (`subject`/`topic`) live only in metadata JSONB and are **never read by
retrieval** — they are dead metadata today.

### 3. Same structure, different use
Single-chunk text rows (structurally identical) span distinct uses across the corpus:
study cards (Annie), personal appointments (Beth, `snapple01`), short notes (Mike). A `$0`
regex heuristic cannot reliably tell them apart (it mis-binned Annie's numbered geometry
items as "documents" and all of Beth's appointments as "documents", the latter because their
NULL `document_id` defeated chunk-counting). Structure alone is insufficient; a *declared,
server-guaranteed* shape signal carries information that cannot be derived for free — but it
must not be left to producer goodwill, which has already produced dead `subject`/`topic`.

## Decision

Adopt a **document model** for `public.thoughts` retrieval and enforce **field integrity at
the ingest chokepoint**. Five coupled decisions:

1. **Key RRF fusion on a unique document identity.**
   - **New `knowledge` path (primary):** build `retrieve_knowledge` keyed on **`knowledge.id`**
     (unique UUID). The `(file, source)` collapse is a `thoughts`-only artifact and must not be
     reproduced in the new retrieval layer.
   - **`thoughts` standby hotfix (optional):** fusion key =
     `COALESCE(NULLIF(document_id, ''), 'id:' || id)` — `document_id` gives parent-document
     rollup (multiple chunk hits from one document merge into one result, best chunk surfaced);
     the `id` fallback prevents NULL-`document_id` rows from collapsing. Keeps the rollback
     target healthy during cutover.

   **Validated** by before/after simulation on `thoughts` (`ingest/diag_sim_rekey.py`) — the
   evidence that the unique-id principle is correct and carries to `knowledge`:
   - mike `terraform state backend`: 4 → 7 results; `MCP contract`: test-junk demoted, real
     doc to #1; SELinux/PXE unchanged. No query lost results; no unrelated rows merged
     (cross-key span check = 0).
   - anneliesepaige `how do I navigate the file system`: 1 → 10 with `cd` correctly #1;
     `stop a running program`: corrected `ps`→`kill` at #1.
   - snapple01 (Beth): 1 → 9 — **only because of the id fallback** (her `document_id` is
     entirely NULL). A naive document_id-only key would have left her account broken.

2. **`document_id` hygiene.** Backfill the 12 NULL `document_id`s in Mike's vault and the 9 in
   Beth's via the existing deterministic `compute_ingest_id`, as a reviewed, read-then-write
   migration. Foundational: the same NULL defect breaks both retrieval and shape inference.

3. **Server-side field integrity in `ingest_payload`.** The chokepoint — not producers —
   guarantees well-formed rows: required fields present and non-trivial; reject or normalize
   rather than silently substituting junk (no "topic = today's date" masquerading as a tag).
   Because all five entry points funnel here, one implementation covers every producer.

4. **Introduce a `shape` field (small enum), derived server-side.** Values:
   `card | document | note | event`. Default is computed in `ingest_payload` from `source_type`
   (`obsidian|pdf|docx → document`; `text|slack → note`) with no model call; the producer may
   override with a more specific value. Stored in metadata JSONB now (zero migration; same
   place `subject`/`topic` live), to be promoted to a first-class column or mapped onto the
   OB2 `knowledge` taxonomy when retrieval/tuning consumes it. **Set server-side so it is
   guaranteed populated regardless of producer behavior.**

5. **Classifiers stay tiered and gated, not always-on.**
   - The existing SafeIngest Haiku gate (`_check_ingest_safety`, `OPENBRAIN_EXTENDED_CHECKS`)
     exists specifically to stop **Annie from using ingestion to circumvent the tutor's
     parental guardrails** (jailbreak SOCRATIC_RULES) — a threat not yet observed in practice.
     It is a *safety* gate, not a field/shape validator; it fails closed-and-silent and has a
     known false-positive history on educational content (the `instruction` pattern was reverted
     as too broad). Given the threat is unobserved and her study content is full of trip-words
     (`kill`, `sudo`, "Superuser Do"), the more likely harm of enabling it is silent loss of her
     cards. It remains **off** here; any change is a separate, tested decision.
   - A *shape/field validator* (LLM tier-2) is retained in the design but fires **only** on
     declared-vs-derived mismatch or ambiguity — never on every ingest. Cost is negligible and
     on the OpenRouter / standalone-key budget, not the interactive Claude plan.

## Consequences

- **Blast radius:** Decision 1 changes the single shared `retrieve_thoughts`, affecting every
  consumer (all three Custom GPTs, Claude MCP, raw API, local `brain_server`). Validated as
  strictly beneficial or neutral for all four owners; must be re-checked against Mike's vault,
  not only Annie's, before merge.
- **Retrieval quality:** results-per-query ceiling rises from current key-diversity to
  document-diversity (mike 87→248, annie 2→65, Beth 1→9 effective). Parent rollup prevents
  multi-chunk documents (up to 23 chunks) from flooding results.
- **`shape` is inert until consumed** — Decision 4 only captures the signal now; tuning
  (owner-profile first, then per-record shape) is follow-on work. Capturing at ingest avoids a
  costly LLM backfill later.
- **No re-embedding in scope.** Faceted multi-vector indexing and synthetic-question
  augmentation (the "multiple index points" direction) are explicitly deferred to a later ADR;
  they require re-embedding and a richer ingest pipeline.
- **Privacy:** making Beth's `snapple01` rows individually retrievable (1→9) intersects the
  open personal-content isolation / RLS concern. Confirm visibility rules before this lands.
- **Validation plan:** re-run `scripts/smoke_checks.py`, `ingest/annie_ingest_validate.py
  --validate-only` (target: fuzzy ≥ 80%, up from 56%), and the `diag_*.py` suite; confirm the
  ADR-002 1000-query harness pass rate does not regress.

## Alternatives considered

- **Key on raw `id` only.** Rejected: loses the parent-document rollup; multi-chunk documents
  would return as many fragments instead of one result.
- **Key on `document_id` only (no fallback).** Rejected: validated to leave Beth's account
  (all-NULL `document_id`) and Mike's 12 NULL rows fully collapsed.
- **Reshape Mike's 679 rows into Annie-style cards.** Rejected: his content is legitimately
  composite (runbooks/ADRs/configs); forcing a study-card mold is lossy and wrong-fit. The
  document model serves both shapes natively; faceting later makes *cards* converge toward the
  multi-chunk document shape, not the reverse.
- **Trust producer-declared fields.** Rejected: no enforcement exists today, and unenforced
  `subject`/`topic` already degraded into dead metadata. Integrity must be server-side.
- **Turn on the existing SafeIngest classifier for shape.** Rejected: wrong job (safety, not
  shape) and silent-data-loss risk on educational content.
