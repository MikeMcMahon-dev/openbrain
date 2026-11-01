-- 015_integrity_events.sql — tutoring-integrity trip-wire event store.
--
-- One row per POSSIBLE integrity concern for a monitored student owner. Two writers:
--   * server_heuristic — api/integrity.py, run by the nightly cron over public.query_log
--   * gpt_self_report  — POST /openbrain_integrity_event (token-bound owner, validated)
-- Severity (priority) and confidence are ALWAYS assigned by the server; the request body
-- cannot set them. Rows carry a short reason and query_log ids, never copied query text or
-- conversation content.
--
-- Deliberately separate from knowledge / query_log: this is monitoring data with its own
-- short retention, not knowledge, and must never surface through retrieval.
--
-- reported_at: set only after the parent email for that row was SENT. A failed send leaves it
-- NULL so the next nightly run retries instead of losing the row.
-- expires_at: retention (30 days, parent decision 2026-11-01). The nightly cron deletes
-- expired rows.

BEGIN;

CREATE TABLE IF NOT EXISTS public.integrity_events (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    owner            text NOT NULL,
    tenant_id        text NOT NULL,
    event_type       text NOT NULL CHECK (event_type IN (
                         'answer_request', 'repeated_bypass', 'possible_answer_disclosure',
                         'worksheet_completion', 'unsupported_confirmation')),
    evidence_source  text NOT NULL CHECK (evidence_source IN ('server_heuristic', 'gpt_self_report')),
    priority         text NOT NULL CHECK (priority IN ('low', 'medium', 'high')),
    confidence       text NOT NULL CHECK (confidence IN ('low', 'medium', 'high')),
    reason           text NOT NULL CHECK (char_length(reason) BETWEEN 1 AND 280),
    subject_hint     text CHECK (char_length(subject_hint) <= 80),
    rule_id          text CHECK (char_length(rule_id) <= 64),
    query_log_ids    uuid[] NOT NULL DEFAULT '{}',
    idempotency_key  text NOT NULL CHECK (char_length(idempotency_key) BETWEEN 1 AND 128),
    occurred_at      timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now(),
    reported_at      timestamptz,
    expires_at       timestamptz NOT NULL DEFAULT now() + interval '30 days',
    CONSTRAINT integrity_events_owner_idem_key UNIQUE (owner, idempotency_key)
);

-- Nightly "what has not been reported yet" scan, and the self-report rate-limit count.
CREATE INDEX IF NOT EXISTS integrity_events_owner_unreported_idx
    ON public.integrity_events (owner, created_at) WHERE reported_at IS NULL;
CREATE INDEX IF NOT EXISTS integrity_events_expires_idx
    ON public.integrity_events (expires_at);

-- No policies: reachable only by roles that bypass RLS (OpenBrain's direct psycopg role).
ALTER TABLE public.integrity_events ENABLE ROW LEVEL SECURITY;

-- Required after 2026-10-30 (see TEMPLATE.sql). service_role only, never anon/authenticated.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.integrity_events TO service_role;

COMMIT;
