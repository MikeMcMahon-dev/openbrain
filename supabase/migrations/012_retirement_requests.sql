-- OB2 Migration 012: retirement_requests — human-approval queue for removals
-- Additive; touches no existing table. Apply via Supabase Dashboard SQL editor.
--
-- APPLIED to production 2026-08-23. See 013 for the target_id FK drop: the `delete`
-- method could never execute while this table's FK pinned its own target.
--
-- WHY: every surface can CREATE; none can CLOSE. `ingest` is exposed on the hosted MCP, the stdio
-- server, and the Custom GPT Actions, but retirement lives only in scripts/ and internal modules
-- (ob2_state, contradiction_detect, the reconcile cron). The single retirement any surface can
-- trigger is the IMPLICIT one — a component-key collision on re-ingest. So an agent can log a job
-- req and cannot mark it closed, and every stale record accumulates as `current` forever.
--
-- The fix is NOT to expose a delete tool. Removal is the one operation that must not be
-- agent-authorised: it is the only irreversible action in the vault (the supersession log is
-- append-only, but a hard DELETE is not). This table is the airlock — an agent may REQUEST a
-- removal with evidence; only a human decides; only an approved request executes.
--
-- FLOW:
--   1. Agent proposes            -> status='pending'    (api/retirement_request.py)
--   2. Mike reviews + decides    -> 'approved'|'denied' (scripts/retirement_review.py)
--   3. Approved request executes -> 'executed'          (same CLI, explicit --execute)
--
-- Denials are REMEMBERED: a denied (target, method) is not re-proposed, mirroring the dismissal
-- memory in 009_contradiction_candidates. Without that, an agent re-surfaces the same rejected
-- removal on every pass and review fatigue does the deleting.
--
-- TWO METHODS, and the default is the reversible one:
--   'retire' — append a supersession_events expiry row (superseding_id NULL). The projection sets
--              status='historical'. Content is preserved and reachable via as_of. PREFERRED.
--   'delete' — hard DELETE of the row + its chunks. ONLY valid when nothing references the target
--              (no events, no supersedes_id parent, no contradiction rows). Irreversible. Note
--              that a 'retire' FORECLOSES a later 'delete': once an immutable event references the
--              row, the non-deferrable FK pins it permanently. Choose deliberately.
--
-- ROLLBACK: DROP TABLE IF EXISTS public.retirement_requests CASCADE;

CREATE TABLE IF NOT EXISTS public.retirement_requests (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- The row proposed for removal. FK is intentional: a request cannot outlive its target, and a
  -- target cannot be hard-deleted out from under a pending request without this surfacing.
  target_id     UUID NOT NULL REFERENCES public.knowledge(id),

  method        TEXT NOT NULL DEFAULT 'retire'
                  CHECK (method IN ('retire', 'delete')),

  -- Vocabulary matches supersession_events.reason_code so an approved 'retire' maps straight
  -- through to the event with no translation layer.
  reason_code   TEXT NOT NULL
                  CHECK (reason_code IN ('explicit', 'component_collision',
                                         'contradiction_confirmed', 'ttl_expiry',
                                         'manual', 'migration')),

  -- WHY this row should go, in prose, written by the requester. This is what Mike actually reads;
  -- a request without a real rationale should be denied on principle.
  rationale     TEXT NOT NULL CHECK (length(btrim(rationale)) >= 20),

  -- Machine-checked facts captured AT REQUEST TIME (reference counts, age, duplicate-of ids).
  -- Evidence goes stale: the CLI re-checks before executing and refuses on drift.
  evidence      JSONB,

  requested_by  TEXT NOT NULL,              -- agent or human identifier
  requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

  status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'denied', 'executed', 'failed')),

  decided_by    TEXT,                       -- NULL until a human decides
  decided_at    TIMESTAMPTZ,
  decision_note TEXT,
  executed_at   TIMESTAMPTZ,

  -- A decision must record who made it. Guards against a code path flipping status with no actor.
  CONSTRAINT decision_has_actor CHECK (
    (status IN ('pending'))
    OR (status = 'failed')
    OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)
  ),
  CONSTRAINT executed_has_timestamp CHECK (
    status <> 'executed' OR executed_at IS NOT NULL
  )
);

-- One OPEN request per (target, method). A denied or executed request stays as history and does
-- not block a genuinely new proposal later; a pending/approved one does.
CREATE UNIQUE INDEX IF NOT EXISTS one_open_request_per_target
  ON public.retirement_requests (target_id, method)
  WHERE status IN ('pending', 'approved');

-- The review queue: oldest first, so nothing rots at the bottom.
CREATE INDEX IF NOT EXISTS retirement_requests_pending_idx
  ON public.retirement_requests (requested_at) WHERE status = 'pending';

-- Denial memory: cheap lookup for "has this already been rejected?"
CREATE INDEX IF NOT EXISTS retirement_requests_denied_idx
  ON public.retirement_requests (target_id, method) WHERE status = 'denied';

COMMENT ON TABLE public.retirement_requests IS
  'Human-approval airlock for vault removals. Agents propose with evidence; only a human decides; '
  'only an approved request executes. Denials are remembered so rejected removals are not '
  're-proposed. method=retire appends an append-only expiry event (preferred, preserves content); '
  'method=delete is irreversible and valid only when nothing references the target.';
