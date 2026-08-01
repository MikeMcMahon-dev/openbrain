# Migration 007 apply runbook — knowledge_chunked → content-only (ADR-018a P2)

Expand/contract. Each step names its go/no-go and rollback. **No prod mutation without Mike's
sign-off.** Read-only dry run below is the evidence for that sign-off.

## Dry-run evidence (read-only, run 2026-08-01)

`scripts/preflight_migration.py knowledge_chunked` + the join-parity probe:

- **1,317** chunk rows; **0 orphans** (every chunk joins to a parent → metadata is always re-derivable).
- Chunk-vs-parent disagreement per column to be dropped: `status 0, component_key 0, tags 0,
  domain 0, environment 0`, `system 8`. The 8 are the instance-#3 drift (chunk NULL, parent
  correct) — the deployed join already serves the parent value, so dropping loses nothing real.
- Real index names verified: `knowledge_chunked_status_domain_idx`, `_system_idx`, `_tags_idx`
  (drop with §B). `status` CHECK `knowledge_chunked_status_check` auto-drops with the column.
- Writers of the table: `api/knowledge_ingest.py` (live mirror) + `scripts/backfill_chunks.py` —
  **both** migrated to content-only in PR #85. No other writers.

**Conclusion:** §B destroys only a duplicate-or-wrong copy of parent metadata; not data loss.

## Preconditions
- [ ] **PR #85 merged** to main (retrieval join, content-only mirror, `component_key` writer).
- [ ] Fresh Supabase backup / PITR confirmed available.

## Steps

**1. Migration 007 §A — `DROP NOT NULL`** (Supabase SQL editor; run §A lines only).
- Effect: makes `domain/environment/status/tags` nullable. Behaviour-neutral — old mirror still writes values.
- Go/no-go: `Success`. 
- Rollback: `ALTER TABLE knowledge_chunked ALTER COLUMN <c> SET NOT NULL;` (still populated).

**2. Deploy** PR #85's code (Vercel auto-deploys on merge to main; confirm the deploy is live).
- Effect: retrieval joins to parent for metadata; mirror writes content-only; `component_key` writer active.
- Go/no-go: deployment status = Ready.
- Rollback: revert the deploy — columns still exist and the reverted mirror still writes them.

**3. Verify BEFORE dropping anything** (this is the gate for §B):
- [ ] `python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app` → all ok, exit 0.
- [ ] `make capability-audit` → P0 invariants hold; no new orphans.
- [ ] Spot-check a chunked query returns correct parent metadata (e.g. a DNS/SpectreNet query;
      `system` populated, right doc #0).
- No-go on any failure → **stop**, do not run §B; the columns are still present so nothing is lost.

**4. Migration 007 §B — `DROP` the six columns + their indexes** (Supabase SQL editor; §B lines).
- Effect: `knowledge_chunked` becomes content-only. Drift becomes structurally impossible.
- Go/no-go: `Success`.
- Rollback: re-add columns + `UPDATE … FROM knowledge` backfill (the §B rollback block in the migration).

**5. Migration 006 §E — `VALIDATE CONSTRAINT component_requires_system`** (the 2 rows are already re-keyed).
- Go/no-go: `Success` (clean scan). Failure names a still-null-system component row → stop, re-key it.

**6. Post-apply cleanup (PR):**
- [ ] Delete `tests/test_chunk_metadata_parity.py` — it xPASSes now (strict) and would fail the build; its job is done.
- [ ] `make test` → green.
- [ ] `chore(lint)` PR for the 163 ruff findings (surgical wraps).

## Acceptance
- `make capability-audit`: `null-system component rows` = 0; `>1 current per component` = 0.
- Parity guard xPASS → deleted. Suite green.
- A re-ingest of a `component:*` doc replaces in place (one current row); its chunks carry no metadata.
