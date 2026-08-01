-- OB2 Migration 008: supersession_events — append-only transition records (ADR-018 P3)
-- Additive; does NOT touch public.thoughts. Apply via Supabase Dashboard SQL editor.
--
-- ⚠⚠ STAGED — NOT YET APPLIED. Needs Mike's sign-off. Sections A–B here (table + backfill);
--    the projection trigger + deferrable guard land in a later trialed section (P3 cont.). ⚠⚠
--
-- WHY: retirement is currently an in-place `UPDATE knowledge SET status='superseded'`
-- (api/knowledge_ingest.py auto_supersede + ob2_state.py confirm). That destroys the record of
-- WHAT retired a row, WHY, and WHEN. This makes each retirement a first-class, immutable event;
-- `knowledge.status='superseded'` becomes a projection of the log (later section), and the log —
-- not the status column — is the source of truth. Recovery is replay, not restore.
--
-- SCOPE (proven against prod 2026-08-01, read-only recon):
--   * The projection owns the current↔superseded transition ONLY. `historical` (67 rows) and
--     `draft` are BIRTH states (bulk OB1→OB2 import / propose flow) — none carry a component_key,
--     supersedes_id, or any incoming link; no event references them, so the projection never
--     touches them. Verified: 0 rows where stored status disagrees with the supersedes_id chain.
--   * environment='Archive' (9 rows) is an orthogonal taxonomy axis (all status='current'),
--     NOT a retirement state — it never co-occurs with historical/superseded. Out of scope.
--
-- ENUM NOTE: the ADR says "enum" conceptually. This schema realises every status-like vocabulary
-- as TEXT + CHECK (knowledge.status, tag_proposals.status) — no CREATE TYPE exists. TEXT+CHECK is
-- also the only form that trials cleanly under sql_trial.py: `ALTER TYPE ... ADD VALUE` cannot be
-- rolled back inside a transaction (ADR-018 §10e HARD GATE). So: TEXT + CHECK, matching convention.
--
-- ROLLBACK (§A+§B): DROP TABLE public.supersession_events CASCADE;  -- new table, no dependents yet.

-- ══ A. supersession_events table ══════════════════════════════════════════════════════════
CREATE TABLE IF NOT EXISTS public.supersession_events (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- The row being retired (always present) and its successor (NULL for a pure expiry).
  -- superseding_id FK is DEFERRABLE INITIALLY DEFERRED: the write path inserts the event
  -- FIRST (so the projection retires the old row before the new current row is inserted,
  -- avoiding a one_current_per_component collision), then inserts the successor with a
  -- pre-generated id. The FK is validated at COMMIT, by which point the successor exists.
  superseded_id  UUID NOT NULL REFERENCES public.knowledge(id),
  superseding_id UUID          REFERENCES public.knowledge(id) DEFERRABLE INITIALLY DEFERRED,

  occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),   -- system/recorded time of the transition

  reason_code    TEXT NOT NULL CHECK (reason_code IN (
                   'explicit', 'component_collision', 'contradiction_confirmed',
                   'ttl_expiry', 'manual', 'migration')),
  reason_note    TEXT,
  actor          TEXT,
  method         TEXT NOT NULL CHECK (method IN ('agent', 'human', 'job', 'backfill')),

  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),   -- when THIS event row was written

  -- A document is retired by exactly one event → projection is deterministic, and a
  -- double-retirement is a bug the DB rejects rather than silently records.
  CONSTRAINT one_retirement_per_document UNIQUE (superseded_id),

  -- A row cannot supersede itself.
  CONSTRAINT no_self_supersession CHECK (superseding_id IS NULL OR superseding_id <> superseded_id)
);

CREATE INDEX IF NOT EXISTS supersession_events_superseding_idx
  ON public.supersession_events (superseding_id) WHERE superseding_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS supersession_events_occurred_idx
  ON public.supersession_events (occurred_at DESC);

COMMENT ON TABLE public.supersession_events IS
  'ADR-018 P3: append-only, immutable record of every knowledge retirement. Source of truth for '
  'knowledge.status=superseded (projected by trigger). Recovery is replay from here, not restore.';

-- Append-only: block UPDATE and DELETE at the table level (app connects as postgres/BYPASSRLS,
-- so RLS cannot enforce this — a trigger can, for every role).
CREATE OR REPLACE FUNCTION public.supersession_events_immutable()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'supersession_events is append-only: % is not permitted', TG_OP
    USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS supersession_events_no_mutate ON public.supersession_events;
CREATE TRIGGER supersession_events_no_mutate
  BEFORE UPDATE OR DELETE ON public.supersession_events
  FOR EACH ROW EXECUTE FUNCTION public.supersession_events_immutable();

-- RLS: service_role full (BYPASSRLS app is unaffected); no anon access to the audit log.
ALTER TABLE public.supersession_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS supersession_events_service_all ON public.supersession_events;
CREATE POLICY supersession_events_service_all ON public.supersession_events
  FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ══ B. Backfill the existing supersedes_id chains as reason_code='migration' ═══════════════
-- Seeds the log from the 5 existing chains (dns-current-state ×1, vlan-switch-topology ×3,
-- one system-null pair). occurred_at = the superseded row's valid_until (= successor valid_from
-- in every existing case). Derived from live data, not hardcoded UUIDs.
INSERT INTO public.supersession_events
  (superseded_id, superseding_id, occurred_at, reason_code, reason_note, actor, method)
SELECT k.supersedes_id, k.id, p.valid_until,
       'migration',
       'Backfilled from knowledge.supersedes_id chain (ADR-018 P3)',
       'p3-backfill', 'backfill'
FROM public.knowledge k
JOIN public.knowledge p ON p.id = k.supersedes_id
WHERE k.supersedes_id IS NOT NULL
ON CONFLICT (superseded_id) DO NOTHING;

-- ══ C. Projection — the SOLE writer of the retirement transition on knowledge.status ═══════
-- Fires AFTER INSERT on the event log. superseding_id present → the retired row becomes
-- 'superseded'; absent (a pure expiry) → 'historical'. Guarded by `status='current'` so it is
-- idempotent and never disturbs the 67 birth-state 'historical'/'draft' rows (which carry no
-- event). No other code path may write status='superseded' once this is live (P3 write-path
-- switch retires the auto_supersede UPDATE and ob2_state.py confirm UPDATE).
CREATE OR REPLACE FUNCTION public.project_supersession()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.superseding_id IS NOT NULL THEN
    UPDATE public.knowledge
       SET status = 'superseded', valid_until = NEW.occurred_at
     WHERE id = NEW.superseded_id AND status = 'current';
  ELSE
    UPDATE public.knowledge
       SET status = 'historical', valid_until = NEW.occurred_at
     WHERE id = NEW.superseded_id AND status = 'current';
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS supersession_events_project ON public.supersession_events;
CREATE TRIGGER supersession_events_project
  AFTER INSERT ON public.supersession_events
  FOR EACH ROW EXECUTE FUNCTION public.project_supersession();
