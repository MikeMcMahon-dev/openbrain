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
1. **Merge PR #72** (ingest-time chunking). ✅ MERGED. If actively ingesting during the trial,
   set `OPENBRAIN_CHUNK_ON_INGEST=1` so reads stay fresh — **still a pending Vercel env flip.**

## Shortfall pass — branch `feat/chunking-shortfalls` (2026-07-30, CC + Chat reconciled)
All four of Chat's points re-adjudicated against the store (row-level, post-backfill). Chat's
4.1 held on the RIGHT metric; 4.4 was already built. Net: 4 real items, 0 refuted.

- **4.1 headingless fat chunks — FIXED in code (chunker).** The refutation was a definitional
  mismatch: "0 docs ≥400w *unchunked*" counted document-level presence in the chunk table, not
  section *splitting*. Row-level truth: 25 chunk rows >400w, **20 are the sole chunk of their
  doc**, max 1009w — headingless multi-topic blobs the `#`-only splitter never divided.
  Fix (`api/chunking.py`): (a) `_split_large` now sentence-windows a single oversized paragraph
  with no blank lines (the 1009w wall); (b) headingless sections are capped at
  `_HEADLESS_MAX_WORDS=400` instead of the 800 ceiling. Dry-run: 800 docs → 1266 chunks (was
  1241), sane +25. **Prod realization = re-chunk backfill (see below) — NOT yet run.**
- **4.2 heading/chunked flag — DONE + verified live.** `_adapt_knowledge_result` now sets
  `chunked` from the RAW document_id (before the id-fallback, which always populates it) +
  carries `chunk_index`. `_skim_result` no longer falls a headingless chunk back to the
  provenance string ("Personal") — heading is real-or-None; adds `chunked`. Legacy rows still
  fall back to `section`.
- **4.3 confidence — DONE + verified live.** New `_confidence_from_signals` keys off cosine
  `vector_similarity` (boost-independent), falls back to the fused-RRF heuristic for keyword-
  only hits. Live check: the query that read `medium` now reads `high` (vsim 0.707).
- **4.4 sibling hint — ALREADY BUILT.** `collapse_chunks` attaches `sibling_chunks`, `_skim_result`
  surfaces it. Known limit: lists only *retrieved* siblings, so a caller seeing 1 of 6 sections
  can't tell 5 more exist. Cheap future add: carry the doc's total chunk count. Not built (adds
  a per-query count roundtrip for marginal value).

Tests: `tests/test_chunking.py` (+5 windowing) and `tests/test_two_stage_retrieval.py` (+6 for
4.2/4.3). Full suite 110 green.

### PENDING PROD OPS (Mike's go/no-go — both mutate prod, neither auto-runs)
1. **Re-chunk backfill for 4.1.** `scripts/backfill_chunks.py --execute --rechunk`. The plain
   `--execute` uses `ON CONFLICT DO NOTHING` and would leave the old fat chunk_0 + only append
   the new tail = corrupt hybrid. `--rechunk` (added this branch) deletes each doc's chunks
   before re-inserting; delete+insert+commit is per-doc so an interrupted run is never hybrid.
   Re-embeds ~1266 chunks (embedding API cost). Dry-run first: `backfill_chunks.py` (no flags).
2. **`OPENBRAIN_CHUNK_ON_INGEST=1`** in Vercel — makes new notes self-chunk on ingest (else the
   chunked store goes stale vs `knowledge`). Independent of #1.

## §5 measurement (optional, low priority)
Broad harness run capturing chunked-vs-unchunked spread. Coverage now measured directly, so low
priority — but re-run after the re-chunk backfill to confirm the 20 fat chunks actually split.

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
