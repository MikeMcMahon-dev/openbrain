# OB2 Cutover Plan — Make `knowledge` the Live Path

**Status:** Phase 0 (planning) — branch `cut/ob2-cutover`
**Date:** 2026-06-17
**Owner sign-offs required:** Supabase manual backup (in progress); data-curation mapping; the flip.

---

## Why this exists

OB2 (ADRs 007–010) was built and stalled before cutover (see memory
`project_ob2_stalled_before_cutover`). Verified live state on 2026-06-17:

- `public.knowledge`: 699 rows, **all `status='historical'`**, newest `created_at` 2026-05-14.
  Embeddings populated (699/699) but **no code reads them**.
- `handle_query_state` (`api/ob2_state.py:277`) is **structured filtering only** — no text
  query param, no vector/keyword/RRF. Semantic retrieval over `knowledge` **does not exist**.
- Family ingest (`chatgpt.py`/`claude.py`/`ingest.py`) writes **`thoughts` only**.
  `/api/ingest_state` is the sole `knowledge` writer and **requires** `domain` ∈
  {Network, K8s, Security, Study, OpenBrain, Personal} + `environment` ∈
  {Production, Lab, Study, Archive} — taxonomy the family GPTs never send.
- The family's daily driver — fuzzy semantic Q&A — runs entirely on legacy `thoughts`,
  which itself has the fusion-key collapse bug (see `project_retrieval_fusion_key_bug` / ADR-011).

**Goal of this cutover:** the family runs on `knowledge` — semantic retrieval over `current`
rows, ingest that writes `knowledge` with validated taxonomy + `shape`, curated data migrated
from `thoughts`, behind a flag, fully reversible.

**Not in scope here:** `OB2-STAGE5-MCP.md` (wiring the 6 OB2 endpoints into the MCP server) is a
separate, later task. ADR-011 is the retrieval + ingest-integrity *spec* this plan consumes.

---

## Phasing

### Phase 0 — Planning (this doc + ADR-011) — me
Branch, shared spec, fan-out contracts. No code, no DB writes.

### Phase 1 — Parallel build (fan-out, isolated worktrees, READ-ONLY DB)
Three independent workstreams against the contracts below. Each delivers code + tests and
**touches no production data**.

**Workstream A — Retrieval over `knowledge`**
- New `retrieve_knowledge(query, n_results, owner, filters)` in `api/_openbrain_api.py`,
  mirroring `retrieve_thoughts` (vector + keyword candidates → RRF fusion + length penalty +
  confidence) BUT:
  - candidate SQL targets `public.knowledge`, default `status = 'current'`.
  - **fusion key = `knowledge.id`** (unique UUID) — ADR-011 decision 1. The
    `(file, source)` collapse must not be reproduced.
  - optional structured pre-filters (domain/environment/system) AND-ed with the semantic search.
  - owner scoping via `created_by`.
- Unit tests with a fixture set; no live write. Returns the same result shape consumers expect
  (`text`, `score`, `confidence`, plus knowledge fields).
- Interface contract (so B/integration can rely on it): `retrieve_knowledge(...) ->
  list[dict]` with keys `{id, text, score, confidence, domain, environment, system, tags, status}`.

**Workstream B — Ingest → `knowledge` with field integrity (ADR-011 decisions 3–4)**
- `write_knowledge(content, owner, taxonomy, shape, ...)` path mirroring `_write_text_ingest`
  but writing `public.knowledge` (embedding + required taxonomy).
- **Server-side taxonomy mapper**: derive `domain`/`environment`/`system`/`tags` from the
  legacy signals (`metadata.subject`/`topic`/`owner`) using the rules in
  `docs/migrations/001_domain_discovery.md`. Reject/normalize — never silently default to junk.
- **`shape` enum** (`card|document|note|event`), derived server-side from `source_type`
  (obsidian|pdf|docx→document; text|slack→note) with optional producer override; stored as a
  `knowledge` column or in a metadata field (decide in B).
- Adapt `chatgpt.py`/`claude.py`/`ingest.py` to route through `write_knowledge` **behind a flag**
  (`OPENBRAIN_WRITE_TARGET=knowledge|thoughts`, default `thoughts` until the flip).
- Handle the duplicate-`current` trigger: study content (`system IS NULL`) is exempt; ops
  content returns 409 → surface the supersession path, don't crash.
- Tests for mapping + integrity + 409 handling.

**Workstream C — Data migration + curation (DRY-RUN ONLY, produces a report)**
- Extend `scripts/migrate_thoughts.py` (verify it exists). Produce a **proposed mapping +
  curation report** for human sign-off covering:
  - the 66 post-migration `thoughts` rows not yet in `knowledge` (incl. Annie's 31 Linux cards),
  - a **current-promotion policy** (which historical rows become `current`),
  - **curation/drop list**: smoke-test junk, malformed subjects, duplicate one-offs (the
    `[malformed subject: ...]` and `smoke test` rows in domain_discovery should NOT migrate),
  - taxonomy assignment per surviving row (reusing B's mapper),
  - `document_id` hygiene (backfill nulls; ADR-011 decision 2).
- **Executes nothing.** Output: `docs/migrations/003_cutover_migration_report.md` + an
  idempotent, reversible migration script gated behind `--execute`.

### Phase 2 — Integration (serial, me + review)
Merge A/B/C. Wire `query_payload` + the GPT/Claude query tools + tutor packet + `query_log`
to `retrieve_knowledge` behind `OPENBRAIN_READ_TARGET=knowledge|thoughts` (default `thoughts`).
Keep both paths runnable for A/B comparison.

### Phase 3 — Validation (serial)
- `scripts/smoke_checks.py` green.
- `ingest/annie_ingest_validate.py --validate-only` against `knowledge` — target fuzzy ≥ 80%.
- 1000-query harness (ADR-002) — no regression vs the 96.9% baseline.
- RLS checks on `knowledge` (INSERT-only agents; service role).
- Personal-content isolation/visibility confirmed (Beth/`snapple01`, Mike's Personal rows).

### Phase 4 — The flip (serial, HUMAN-GATED)
Preconditions: backup confirmed; migration report signed off; Phases 2–3 green.
1. Run migration `--execute` (DB write).
2. Set `OPENBRAIN_WRITE_TARGET=knowledge`, `OPENBRAIN_READ_TARGET=knowledge` in Vercel.
3. Post-flip smoke + live validation.

---

## Rollback
- Data: `DELETE FROM public.knowledge WHERE source LIKE 'cutover:%'` (migration tags every row).
- Code/traffic: flip `OPENBRAIN_READ_TARGET`/`OPENBRAIN_WRITE_TARGET` back to `thoughts`.
  `thoughts` is left intact and writable throughout — it remains the hot standby.
- The optional `thoughts` fusion-key hotfix (ADR-011) keeps the standby healthy if we roll back.

## Human gates
1. Supabase manual backup (free tier — no scheduled backups). **In progress (Mike).**
2. Sign off `docs/migrations/003_cutover_migration_report.md` (curation + promotion).
3. Approve the Phase 4 flip.

## Open decisions for Mike
- **Curation aggressiveness:** drop all smoke-test/malformed rows? (recommend yes.)
- **Personal content:** migrate the mental-health "Core Wound"/"Betrayal" rows to
  `domain=Personal, environment=Archive`, or exclude from the family-visible system entirely?
- **`thoughts` hotfix:** ship the fusion-key fix as standby insurance, or skip and rely on the flip?
