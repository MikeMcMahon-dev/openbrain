-- OB2 Migration 004: tag proposal review queue (taxonomy governance, ADR-012)
-- Stage 2+ — additive only, does NOT touch public.thoughts or public.knowledge
-- Apply via Supabase Dashboard SQL editor or: supabase db push
-- Apply AFTER 003_tag_vocabulary.sql.
--
-- Purpose: the "DB has these tags; use one, or make new" review queue. When an
-- ingest carries a tag outside the controlled vocabulary (api/taxonomy_map.py
-- CANONICAL_TAGS, runtime-backed by public.tag_vocabulary), the ingest path does
-- NOT silently drop it and does NOT write it to knowledge.tags. It records the
-- unknown tag here as a pending proposal. A human reviews the queue via
-- scripts/tag_review.py and either:
--   * --approve  -> INSERT the tag into public.tag_vocabulary (status='approved')
--   * --remap    -> map the raw tag onto an existing canonical tag (status='remapped')
--   * --reject   -> retire the proposal (status='rejected')
--
-- This table is the queue only; approvals/remaps mutate tag_vocabulary / knowledge.
-- It never enforces anything on writes (no trigger) — it is observational.

-- ── Table ─────────────────────────────────────────────────────────────────────
-- IF NOT EXISTS makes this re-runnable. normalized_key is the _tag_key-folded form
-- (lowercase, space/_ -> -) so casing/spacing variants of the same raw tag collapse
-- onto one queue row whose occurrences increment.

CREATE TABLE IF NOT EXISTS public.tag_proposals (
  normalized_key  TEXT PRIMARY KEY,                 -- _tag_key(raw_tag): folded dedup key
  raw_tag         TEXT,                             -- most-recently-seen raw form as sent
  occurrences     INT NOT NULL DEFAULT 1,           -- bumped on every re-sighting
  first_seen      TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen       TIMESTAMPTZ NOT NULL DEFAULT now(),
  sample_owner    TEXT,                             -- owner from a representative sighting
  sample_context  TEXT,                             -- short content/subject excerpt for review
  status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'approved', 'rejected', 'remapped')),
  remap_to        TEXT                              -- canonical target when status='remapped'
);

-- ── Index ───────────────────────────────────────────────────────────────────--
-- The review CLI lists pending proposals newest-activity-first; index the common path.

CREATE INDEX IF NOT EXISTS tag_proposals_status_idx
  ON public.tag_proposals (status, last_seen DESC);

-- ── Row Level Security ──────────────────────────────────────────────────────--
-- Mirror tag_vocabulary/wiki_pages: service_role full access; anon read-only so the
-- queue is inspectable. The recording UPSERT and all mutations go through service_role.

ALTER TABLE public.tag_proposals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tag_proposals_service ON public.tag_proposals;
CREATE POLICY tag_proposals_service ON public.tag_proposals
  FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS tag_proposals_anon_read ON public.tag_proposals;
CREATE POLICY tag_proposals_anon_read ON public.tag_proposals
  FOR SELECT TO anon USING (true);

-- ── Rollback ──────────────────────────────────────────────────────────────────
-- Run these statements (in this order) to fully reverse this migration:
--
--   DROP POLICY IF EXISTS tag_proposals_anon_read ON public.tag_proposals;
--   DROP POLICY IF EXISTS tag_proposals_service ON public.tag_proposals;
--   DROP INDEX IF EXISTS public.tag_proposals_status_idx;
--   DROP TABLE IF EXISTS public.tag_proposals;
