-- OB2 Migration 007: knowledge_chunked becomes content-only; metadata joins from parent
-- (ADR-018a Decision item 3). SUPERSEDES migration 006 §A's addition of component_key to
-- knowledge_chunked, and removes the five other mirrored metadata columns.
--
-- APPLIED to production (date unrecorded — predates migration_log.md; verified 2026-08-23 by
-- catalog inspection: all six mirrored columns are gone from knowledge_chunked). Two sections,
-- applied AROUND a code deploy (expand/contract), because domain/environment/status/tags are
-- NOT NULL and the mirror INSERT can only stop writing them once they are nullable.
--
-- WHY: knowledge_chunked hand-mirrored knowledge's metadata (system, status, component_key,
-- tags, domain, environment) via app code; anything synced by code a human must remember to
-- update drifts (3 instances in 9 days, incl. the P2 re-key). ADR-018a: chunks own only
-- content + embedding + identity + created_by; metadata JOINs from the parent knowledge row
-- on document_id, so it is single-sourced and cannot drift.
--
-- ⚠ CRITICAL APPLY ORDER (each step names its rollback) ⚠
--   1. §A — DROP NOT NULL on the four NOT-NULL mirrored columns.   Rollback: re-add NOT NULL
--      (safe: still populated). Behaviour-neutral; the old mirror keeps writing values.
--   2. DEPLOY the code (feat/p2-chunked-join-metadata): retrieval joins to the parent for
--      metadata; the mirror writes content-only; component_key gets its writer. Reversible:
--      revert the deploy (columns still exist, values still written by the reverted mirror).
--   3. §B — DROP the six now-unread, now-unwritten metadata columns + their indexes.
--      Rollback: this is the point of no easy return — see the guard below; take the dry run
--      and a fresh backup first. After §B the parity guard test xPASSes (delete it).
-- Do NOT run §B until step 2 is deployed AND verified: a live old-mirror INSERT after §B
-- would fail (writing to dropped columns), and retrieval would 500 on the missing columns.

-- ══ A. Run BEFORE the deploy: make the four NOT-NULL mirrored columns nullable ════════════
-- system + component_key are already nullable (006). These four are NOT NULL, so the
-- content-only mirror cannot omit them until this runs.
ALTER TABLE public.knowledge_chunked ALTER COLUMN domain      DROP NOT NULL;
ALTER TABLE public.knowledge_chunked ALTER COLUMN environment DROP NOT NULL;
ALTER TABLE public.knowledge_chunked ALTER COLUMN status      DROP NOT NULL;
ALTER TABLE public.knowledge_chunked ALTER COLUMN tags        DROP NOT NULL;

-- ══ B. Run AFTER the deploy is verified: drop the six mirrored metadata columns ═══════════
-- Confirm the deploy is live and `make capability-audit` + smoke are green first.
-- The three indexes that reference the dropped columns (names VERIFIED against pg_indexes
-- 2026-08-01, not guessed): status_domain_idx (status,domain,environment), system_idx
-- (system,status), tags_idx (tags). Postgres would auto-drop them with the columns anyway;
-- dropping them explicitly first keeps the intent legible. The embedding (HNSW), document,
-- created_by, and doc_chunk_uniq indexes are retained — they reference kept columns.
--
-- AS-APPLIED (2026-08-01): two RLS policies gated on `status` and blocked the column drop —
-- found at apply time because the preflight did not enumerate policies (now fixed). The app
-- connects as `postgres` (BYPASSRLS), so these anon policies governed only the unused Supabase
-- REST anon surface; dropped them (Mike's call) rather than re-point them at the parent.
DROP POLICY IF EXISTS knowledge_chunked_anon_read ON public.knowledge_chunked;
DROP POLICY IF EXISTS knowledge_chunked_agent_insert ON public.knowledge_chunked;

DROP INDEX IF EXISTS public.knowledge_chunked_status_domain_idx;
DROP INDEX IF EXISTS public.knowledge_chunked_system_idx;
DROP INDEX IF EXISTS public.knowledge_chunked_tags_idx;

-- Dropping `status` auto-drops the CHECK `knowledge_chunked_status_check` that references it
-- (verified via preflight_migration.py — it depends only on status, so no CASCADE needed).
ALTER TABLE public.knowledge_chunked DROP COLUMN IF EXISTS status;
ALTER TABLE public.knowledge_chunked DROP COLUMN IF EXISTS system;
ALTER TABLE public.knowledge_chunked DROP COLUMN IF EXISTS component_key;
ALTER TABLE public.knowledge_chunked DROP COLUMN IF EXISTS tags;
ALTER TABLE public.knowledge_chunked DROP COLUMN IF EXISTS domain;
ALTER TABLE public.knowledge_chunked DROP COLUMN IF EXISTS environment;

-- ── Rollback of §B (expensive — data is gone from chunks; re-derivable from the parent) ────
-- The metadata was a DUPLICATE of the parent, so it is not lost: re-add the columns and
-- backfill from knowledge on document_id. Provided so nobody improvises under pressure.
--   ALTER TABLE public.knowledge_chunked ADD COLUMN status TEXT, ADD COLUMN system TEXT,
--     ADD COLUMN component_key TEXT, ADD COLUMN tags TEXT[], ADD COLUMN domain TEXT,
--     ADD COLUMN environment TEXT;
--   UPDATE public.knowledge_chunked kc SET status=k.status, system=k.system,
--     component_key=k.component_key, tags=k.tags, domain=k.domain, environment=k.environment
--     FROM public.knowledge k WHERE k.id = kc.document_id;
--   -- then re-add NOT NULL on domain/environment/status/tags if desired (§A reverse).

-- ══ C. AS-APPLIED before 006 §E VALIDATE (2026-08-01) ═════════════════════════════════════
-- Promoting 006's `component_requires_system` CHECK to VALID failed: two `component:adr-018`
-- rows (1 current, 1 superseded) had a backfilled component_key but no system. They are ADR
-- handoff artifacts — "ingested once, not maintained live" (ADR-018a) — so they should not be
-- live component identities. De-registered them (clear the key) rather than invent a system,
-- then 006 §E validated clean. Trialed in a rolled-back txn first.
--   UPDATE public.knowledge SET component_key = NULL WHERE component_key='adr-018' AND system IS NULL;
--   ALTER TABLE public.knowledge VALIDATE CONSTRAINT component_requires_system;   -- (006 §E)
