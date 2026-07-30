# Handoff — OpenBrain retrieval / chunking (2026-07-30)

Read this + ADR-014/015/016/017 before touching retrieval. Context for a fresh session.

## What is LIVE in prod right now
- **Chunked reads:** `OPENBRAIN_KNOWLEDGE_TABLE=knowledge_chunked` is SET in Vercel — prod
  serves chunked sections. Verified live (search→fetch returns sections). Rollback = unset it.
- **Component boost:** `OPENBRAIN_COMPONENT_BOOST=2.0` set in prod (applies on chunked too).
- **skim→fetch:** works on chunked (fetch by chunk id → section; by document_id → whole doc).
- **IVFFlat recall fix** (probes=10 + pool floor 50) on `knowledge`; chunked table uses HNSW.

## Shipped (merged): ADR-014 instrument, ADR-015 recall, ADR-016 skim/fetch + de-dup,
ADR-017 chunking pipeline + read path + fetch fix. PRs #66,67,68,69,70,71 merged.
- **PR #72 (OPEN):** ingest-time dual-write into knowledge_chunked, gated OFF
  (`OPENBRAIN_CHUNK_ON_INGEST`). Merge, then set the flag = permanent cutover (self-fresh).

## The store
- `public.knowledge` (800 rows, canonical, UNTOUCHED) + `public.knowledge_chunked`
  (1241 rows, backfilled). knowledge_chunked = static snapshot until the ingest flag is on.
- **Staleness caveat:** while ingest flag is OFF, new notes hit `knowledge` only and are NOT
  in the chunked store being read. Re-sync: `python scripts/backfill_chunks.py --execute`
  (idempotent) OR set `OPENBRAIN_CHUNK_ON_INGEST=1`.

## Remaining actions (verified, prioritized)
1. **Merge PR #72** (ingest-time chunking). If actively ingesting during the trial, set
   `OPENBRAIN_CHUNK_ON_INGEST=1` after merge so reads stay fresh.
2. **[small] 4.2 heading/chunked flag.** `_skim_result` falls back `heading or section`, so
   no-heading chunks return the provenance string ("Personal") as a heading. Fix: `heading`
   = the real heading or null; add `"chunked": document_id is not None`. Touch `_skim_result`
   (+ maybe `_adapt_knowledge_result`) in `api/_openbrain_api.py`.
3. **[small] 4.3 confidence.** Top boosted result reads `medium` because `_confidence_label`
   needs rank1↔rank2 separation ≥ 0.004 on the FUSED RRF score, which the ×2 boost compresses
   (0.03279 vs 0.03200 = 0.0008). Decide: compute confidence on `vector_similarity` instead of
   the fused ordinal, or relax separation for boosted/chunked rows. `_confidence_label` in
   `api/_openbrain_api.py`.
4. **[optional] §5 measurement.** Broad harness run capturing chunked-vs-unchunked spread —
   but coverage already verified good (0 long docs unchunked), so low priority.

## REFUTED by data (do NOT act on Chat's report for these)
- 4.1 "coverage gap / add numbered-list detection" — 0 docs ≥400w unchunked. Not needed.
- 4.4 "collapse floods with siblings" — max 1 chunk/doc per result set. Already safe.
- Chat's reports need verification; its headline items here were over-stated (pre-cutover data).

## Gotchas for the next session
- **Migrations:** apply via Supabase SQL editor, NOT `supabase db push` (CLI history stale at
  2026-03-15; OB2 migrations were hand-applied). psycopg direct-execute also fine.
- **Tests:** run `make test` (or `cd tests && python -m pytest`) — the repo-root `vault/`
  symlink breaks bare pytest for the `claude` user. 2 `@needs_db` anti-collapse tests are
  transient-pooler flaky; pass in isolation.
- **DB conn:** `.env.local` SUPABASE_DB_URL is the 6543 transaction pooler; `SET LOCAL` only.
- **Backup:** full `knowledge` snapshot at `/Users/Shared/openbrain-backups/knowledge_20260730-2125.jsonl`
  (800 rows, restorable JSONL).
- **Prod verify pattern:** POST /mcp/messages with the mike.mcmahon67 bearer token from
  OPENBRAIN_TOKEN_OWNER_MAP (never print it).
- **Chunking is HNSW** on the chunked table — no probes tuning needed there.
