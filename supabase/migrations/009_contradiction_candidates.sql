-- OB2 Migration 009: contradiction_candidates — human-review queue (ADR-018 P4)
-- Additive; does NOT touch public.thoughts. Apply via Supabase Dashboard SQL editor.
--
-- ⚠⚠ STAGED — NOT YET APPLIED. Needs Mike's sign-off. All trialed in BEGIN..ROLLBACK. ⚠⚠
--
-- WHY: ADR §7 — same-system, high-similarity `current` pairs are surfaced as CANDIDATES for a
-- human to judge. NOT automated contradiction judgment: two similar rows may both be true (the P0
-- baseline confirmed the top same-system pairs are same-topic / temporal-sequence / append-only
-- session logs, not logical contradictions). Confirmation writes a supersession event
-- (reason_code='contradiction_confirmed') on the P3 rails; DISMISSAL writes nothing AND must not
-- re-flag — so a dismissed/confirmed pair is remembered here and detection skips it. This table
-- is the dismissal memory that keeps the review queue from re-surfacing the same noise nightly.
--
-- SCOPE: same-system pairs only (system is the identity pivot). The 679 null-system floaters are
-- out of scope (recency-net territory, P1), matching ADR §6/§7.
--
-- ROLLBACK: DROP TABLE IF EXISTS public.contradiction_candidates CASCADE;

CREATE TABLE IF NOT EXISTS public.contradiction_candidates (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- The pair, in canonical order (id_lo < id_hi) so a pair is tracked exactly once regardless
  -- of which side detection saw first.
  id_lo        UUID NOT NULL REFERENCES public.knowledge(id),
  id_hi        UUID NOT NULL REFERENCES public.knowledge(id),

  system       TEXT NOT NULL,               -- both rows share this (the identity pivot)
  similarity   REAL NOT NULL,               -- cosine similarity at detection time

  status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'confirmed', 'dismissed')),
  detected_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  reviewed_at  TIMESTAMPTZ,
  reviewer     TEXT,
  note         TEXT,

  CONSTRAINT contradiction_pair_order CHECK (id_lo < id_hi),
  CONSTRAINT one_row_per_pair UNIQUE (id_lo, id_hi)
);

-- The review queue: pending pairs, newest/strongest first.
CREATE INDEX IF NOT EXISTS contradiction_candidates_pending_idx
  ON public.contradiction_candidates (similarity DESC) WHERE status = 'pending';

COMMENT ON TABLE public.contradiction_candidates IS
  'ADR-018 P4: same-system high-similarity current-pair candidates for human review. Dismissal '
  'memory — a confirmed/dismissed pair is never re-flagged. Confirmation writes a '
  'contradiction_confirmed supersession event; dismissal writes nothing.';

ALTER TABLE public.contradiction_candidates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS contradiction_candidates_service_all ON public.contradiction_candidates;
CREATE POLICY contradiction_candidates_service_all ON public.contradiction_candidates
  FOR ALL TO service_role USING (true) WITH CHECK (true);
