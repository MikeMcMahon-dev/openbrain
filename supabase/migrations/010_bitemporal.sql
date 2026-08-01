-- OB2 Migration 010: bitemporality — decouple valid-time from system-time (ADR-018 P5, pulled fwd)
-- Additive; does NOT touch public.thoughts. Apply via Supabase Dashboard SQL editor.
--
-- ⚠⚠ STAGED — NOT YET APPLIED. Needs Mike's sign-off. Trialed in BEGIN..ROLLBACK. ⚠⚠
--
-- WHY: it's a lab — state churns, and every change must override cleanly while the PRIOR truth
-- stays recoverable with its REAL lifespan. Today two clocks are conflated:
--   * system-time (immutable audit): knowledge.created_at, supersession_events.occurred_at
--   * valid-time  (the fact's real lifespan): knowledge.valid_from, knowledge.valid_until
-- valid_from always equals created_at, and the P3 projection stamps valid_until = occurred_at —
-- so a change that happened BEFORE we recorded it stores the wrong lifespan. This wires the two
-- apart: a retirement can carry a backdated fact-offset, independent of when it was recorded.
-- (P5 pulled forward deliberately: the caller is not speculative — lab changes are a standing
-- condition, and building ahead of a known-coming caller is the opposite of component_key's
-- "capability with no caller". valid_from at the ingest surface + fact_valid_until on the retire
-- path get their explicit callers in the same PR, api/knowledge_ingest.py + api/ob2_state.py +
-- api/contradiction_detect.py.)
--
-- ROLLBACK:
--   ALTER TABLE public.knowledge DROP CONSTRAINT IF EXISTS knowledge_valid_span;
--   ALTER TABLE public.supersession_events DROP COLUMN IF EXISTS fact_valid_until;
--   -- and re-apply migration 008 §C to restore the occurred_at-only projection.

-- ══ A. fact-offset on the event log (valid-time, distinct from occurred_at = system-time) ═════
-- Nullable: when NULL the projection falls back to occurred_at, so every existing event (the 5
-- backfilled + all P3/P4 events) keeps its current behaviour. When set, it is the moment the
-- superseded fact actually stopped being true — possibly earlier (backdated) or later than when
-- the retirement was recorded.
ALTER TABLE public.supersession_events
  ADD COLUMN IF NOT EXISTS fact_valid_until TIMESTAMPTZ;

COMMENT ON COLUMN public.supersession_events.fact_valid_until IS
  'ADR-018 P5: valid-time fact-offset — when the superseded fact stopped being true, if different '
  'from occurred_at (system-time). NULL => projection uses occurred_at.';

-- ══ B. Projection honours the fact-offset (the SOLE writer of the retirement transition) ══════
CREATE OR REPLACE FUNCTION public.project_supersession()
RETURNS TRIGGER AS $$
BEGIN
  -- valid-time offset: the backdated fact-offset if given, else the system-time it was recorded.
  IF NEW.superseding_id IS NOT NULL THEN
    UPDATE public.knowledge
       SET status = 'superseded', valid_until = COALESCE(NEW.fact_valid_until, NEW.occurred_at)
     WHERE id = NEW.superseded_id AND status = 'current';
  ELSE
    UPDATE public.knowledge
       SET status = 'historical', valid_until = COALESCE(NEW.fact_valid_until, NEW.occurred_at)
     WHERE id = NEW.superseded_id AND status = 'current';
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

-- ══ C. Lifespan sanity — a fact cannot end before it began ════════════════════════════════════
-- Verified 0 existing rows violate this (valid_until IS NULL for current rows; the 5 superseded
-- rows have valid_until = successor valid_from > their own valid_from). Also guards backdating: a
-- fact_valid_until earlier than the row's valid_from is rejected, not silently stored. NOT VALID
-- then VALIDATE per the established careful pattern (006 §C/§E), though the table is tiny.
ALTER TABLE public.knowledge
  ADD CONSTRAINT knowledge_valid_span
  CHECK (valid_until IS NULL OR valid_from <= valid_until) NOT VALID;

ALTER TABLE public.knowledge VALIDATE CONSTRAINT knowledge_valid_span;
