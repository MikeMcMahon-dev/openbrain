-- OB2 Migration 003: controlled tag vocabulary (taxonomy governance, ADR-012)
-- Stage 2+ — additive only, does NOT touch public.thoughts
-- Apply via Supabase Dashboard SQL editor or: supabase db push
--
-- ⚠ CRITICAL APPLY ORDER ⚠
-- This migration MUST be applied AFTER scripts/retag_knowledge.py has normalized
-- the existing public.knowledge rows. The validation trigger below rejects any row
-- whose tags array contains a value outside tag_vocabulary. The live corpus has
-- drifted (two tag schemes + casing dups), so applying this BEFORE the retag pass
-- would cause the trigger to reject the existing drifted tags on the next UPDATE
-- (and the seed itself would not retroactively fix stored rows). Sequence is:
--   1. scripts/retag_knowledge.py  (folds stored tags to CANONICAL_TAGS)
--   2. this migration              (installs vocabulary + enforcement trigger)
--
-- Source of truth for the seeded vocabulary is api/taxonomy_map.py CANONICAL_TAGS.
-- SQL cannot import Python, so the set is transcribed below. If CANONICAL_TAGS
-- changes, update both — the Python set and the INSERT block here must stay in sync.

-- ── Table ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.tag_vocabulary (
  tag         TEXT PRIMARY KEY,
  added_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Seed: members of CANONICAL_TAGS (api/taxonomy_map.py) ────────────────────────
-- ON CONFLICT DO NOTHING keeps this re-runnable and lets a human add tags out-of-band.

INSERT INTO public.tag_vocabulary (tag) VALUES
  -- Tech / skills (Stage-2 content tags)
  ('IaC'), ('Terraform'), ('Ansible'), ('Bash'), ('Python'), ('RedHat'), ('Bare-Metal'),
  ('Proxmox'), ('K8s'), ('CKA'), ('Network'), ('Security'), ('Architecture'), ('AI'),
  ('Engineering'), ('Reference'), ('Ops'), ('Lab'), ('Production'),
  -- Systems / projects
  ('OpenBrain'), ('Homelab'), ('SpectreNet'), ('PMX-01'), ('ProjectDocs'), ('AgentLab'),
  ('MultiAgentLab'), ('PortfolioBlog'), ('Session'),
  -- People / personal
  ('Personal'), ('Mike'), ('Beth'), ('Annie'), ('Family'),
  -- Study subjects
  ('Science'), ('Biology'), ('Geometry'), ('Math'), ('Study'), ('Linux'),
  -- Life
  ('Health'), ('Nutrition'), ('FoodLog'), ('Preferences'), ('Mental-health'),
  -- Career / interview (cross-cutting)
  ('Career'), ('Interview'), ('NV-Prep'), ('Testing'), ('Ubuntu Study'), ('Schooling')
ON CONFLICT (tag) DO NOTHING;

-- ── Vocabulary enforcement trigger ──────────────────────────────────────────────
-- Rejects any INSERT/UPDATE on public.knowledge whose tags array contains a value
-- not present in tag_vocabulary. Empty array ('{}') and NULL tags pass (nothing to
-- check). The exception names the first offending tag for a clear error message.
-- CREATE OR REPLACE makes the function re-runnable.

CREATE OR REPLACE FUNCTION public.validate_knowledge_tags()
RETURNS TRIGGER AS $$
DECLARE
  bad_tag TEXT;
BEGIN
  -- NULL or empty tags: nothing to validate.
  IF NEW.tags IS NULL OR cardinality(NEW.tags) = 0 THEN
    RETURN NEW;
  END IF;

  -- Namespaced functional tags (e.g. shape:<v> from ADR-011, component:<id> from
  -- ADR-008 dedup) are exempt: they are not descriptive vocabulary. Only validate
  -- plain descriptive tags against tag_vocabulary.
  SELECT t INTO bad_tag
  FROM unnest(NEW.tags) AS t
  WHERE position(':' IN t) = 0
    AND NOT EXISTS (
      SELECT 1 FROM public.tag_vocabulary v WHERE v.tag = t
    )
  LIMIT 1;

  IF bad_tag IS NOT NULL THEN
    RAISE EXCEPTION
      'Tag not in controlled vocabulary: %. Allowed tags live in public.tag_vocabulary (seeded from api/taxonomy_map.py CANONICAL_TAGS). Normalize via normalize_tags() or add the tag intentionally.',
      bad_tag;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- DROP first so the trigger definition is idempotent on re-run (CREATE TRIGGER has
-- no OR REPLACE; the function above does).
DROP TRIGGER IF EXISTS knowledge_tags_validation ON public.knowledge;

CREATE TRIGGER knowledge_tags_validation
  BEFORE INSERT OR UPDATE ON public.knowledge
  FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_tags();

-- ── Rollback ──────────────────────────────────────────────────────────────────
-- Run these statements (in this order) to fully reverse this migration:
--
--   DROP TRIGGER IF EXISTS knowledge_tags_validation ON public.knowledge;
--   DROP FUNCTION IF EXISTS public.validate_knowledge_tags();
--   DROP TABLE IF EXISTS public.tag_vocabulary;
