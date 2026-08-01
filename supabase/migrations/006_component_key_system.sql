-- OB2 Migration 006: component_key column + system namespace vocabulary (ADR-018 P2)
-- Additive; does NOT touch public.thoughts. Apply via Supabase Dashboard SQL editor.
--
-- ⚠⚠ STAGED — NOT YET APPLIED. Needs Mike's sign-off AND the two prerequisites below. ⚠⚠
--
-- WHY: `component_key` lives as an unvalidated `component:*` tag and `system` was settable
-- only in write_knowledge() (no ingest surface), so the (system, component) supersession
-- identity was unsatisfiable on purpose — a null system slipped through and a re-ingest
-- duplicated instead of superseding (ADR-018 Q1b / ADR-019 "capability without a caller").
-- This promotes the key to a real column, seeds the namespace vocabulary, and makes the
-- invariant (at most one current row per component, system never null when keyed) a
-- DATABASE guarantee rather than a convention.
--
-- ⚠ CRITICAL APPLY ORDER ⚠
--   1. Bless the `system` vocabulary values below (esp. FlightSim / MikeMcMahon-Dev for the
--      two currently null-system component rows) — Mike's taxonomy call.
--   2. Apply sections A–D of this migration (column, vocab, NOT-VALID check, unique index).
--   3. Re-key the two null-system component rows to a blessed system (hand corrections,
--      validated by the CHECK as each is made — see ADR-018 P2 sequence).
--   4. Run section E: VALIDATE the CHECK constraint against the (now clean) existing rows.
-- The CHECK is added NOT VALID in step 2 so it enforces NEW writes immediately without
-- failing on the two pre-existing violators; step 4 promotes it to full enforcement once
-- they are corrected.

-- ══ A. component_key column + backfill from the component:* tag ═══════════════════════════
ALTER TABLE public.knowledge      ADD COLUMN IF NOT EXISTS component_key TEXT;
ALTER TABLE public.knowledge_chunked ADD COLUMN IF NOT EXISTS component_key TEXT;

-- Backfill from the existing tag (value after 'component:'). Keep the tag in place for now
-- (retrieval's boost still reads it); the code migration to read the column is a later step.
UPDATE public.knowledge k
SET component_key = (SELECT substring(t FROM 11) FROM unnest(k.tags) t WHERE t LIKE 'component:%' LIMIT 1)
WHERE component_key IS NULL
  AND EXISTS (SELECT 1 FROM unnest(k.tags) t WHERE t LIKE 'component:%');

UPDATE public.knowledge_chunked kc
SET component_key = (SELECT substring(t FROM 11) FROM unnest(kc.tags) t WHERE t LIKE 'component:%' LIMIT 1)
WHERE component_key IS NULL
  AND EXISTS (SELECT 1 FROM unnest(kc.tags) t WHERE t LIKE 'component:%');

-- ══ B. system namespace vocabulary (mirror of tag_vocabulary; ADR-018 P2) ═════════════════
-- Source of truth: api/canonical_systems.py CANONICAL_SYSTEMS. SQL can't import Python —
-- keep the two in sync. `system` is a general NAMESPACE (Mike's ruling), not infra-systems.
CREATE TABLE IF NOT EXISTS public.system_vocabulary (
  system    TEXT PRIMARY KEY,
  added_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.system_vocabulary (system) VALUES
  ('SpectreNet'), ('PMX-01'), ('OpenBrain'), ('Annie')
  -- ADD the blessed values for the two null-system component rows before step 3, e.g.
  -- ('FlightSim'), ('MikeMcMahon-Dev')   -- confirm/rename first (Mike's call)
ON CONFLICT (system) DO NOTHING;

-- Enforce: a non-null `system` must be a registered namespace. NULL passes (nothing keyed).
CREATE OR REPLACE FUNCTION public.validate_knowledge_system()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.system IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM public.system_vocabulary v WHERE v.system = NEW.system) THEN
    RAISE EXCEPTION
      'System not in namespace vocabulary: %. Allowed systems live in public.system_vocabulary (seed: api/canonical_systems.py).',
      NEW.system;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS knowledge_system_validation ON public.knowledge;
CREATE TRIGGER knowledge_system_validation
  BEFORE INSERT OR UPDATE ON public.knowledge
  FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_system();

ALTER TABLE public.system_vocabulary ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS system_vocabulary_service ON public.system_vocabulary;
CREATE POLICY system_vocabulary_service ON public.system_vocabulary
  FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS system_vocabulary_anon_read ON public.system_vocabulary;
CREATE POLICY system_vocabulary_anon_read ON public.system_vocabulary
  FOR SELECT TO anon USING (true);

-- ══ C. Incomplete-identity CHECK (NOT VALID until the two violators are re-keyed) ═════════
-- A component key with no system is an incomplete identity. NOT VALID enforces new writes
-- immediately without failing on the two pre-existing null-system component rows.
ALTER TABLE public.knowledge
  ADD CONSTRAINT component_requires_system
  CHECK (component_key IS NULL OR system IS NOT NULL) NOT VALID;

-- ══ D. One current row per component — the invariant, DB-enforced ═════════════════════════
-- NULLS NOT DISTINCT (PG15+): standard NULL-distinctness treats two NULL systems as different,
-- so (NULL,'k') twice would NOT collide and both would be permitted — the exact duplicate
-- this closes. With the CHECK above, system is never null when keyed anyway; this is the belt.
CREATE UNIQUE INDEX IF NOT EXISTS one_current_per_component
  ON public.knowledge (system, component_key) NULLS NOT DISTINCT
  WHERE status = 'current' AND component_key IS NOT NULL;

-- ══ E. Run AFTER step 3 (the two rows re-keyed): promote the CHECK to full enforcement ════
--   ALTER TABLE public.knowledge VALIDATE CONSTRAINT component_requires_system;

-- ── Rollback (reverse order) ──────────────────────────────────────────────────────────────
--   DROP INDEX IF EXISTS public.one_current_per_component;
--   ALTER TABLE public.knowledge DROP CONSTRAINT IF EXISTS component_requires_system;
--   DROP TRIGGER IF EXISTS knowledge_system_validation ON public.knowledge;
--   DROP FUNCTION IF EXISTS public.validate_knowledge_system();
--   DROP POLICY IF EXISTS system_vocabulary_anon_read ON public.system_vocabulary;
--   DROP POLICY IF EXISTS system_vocabulary_service ON public.system_vocabulary;
--   DROP TABLE IF EXISTS public.system_vocabulary;
--   ALTER TABLE public.knowledge_chunked DROP COLUMN IF EXISTS component_key;
--   ALTER TABLE public.knowledge          DROP COLUMN IF EXISTS component_key;
