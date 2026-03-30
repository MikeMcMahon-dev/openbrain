-- Migration 001: query_log table
-- Run in Supabase SQL Editor
-- Captures every query call for parent reporting, session analysis, and injection flagging.

CREATE TABLE IF NOT EXISTS public.query_log (
    id          uuid        DEFAULT gen_random_uuid() PRIMARY KEY,
    owner       text        NOT NULL,
    tenant_id   text        NOT NULL,
    query_text  text,
    result_count int,
    mode        text,
    flagged     boolean     DEFAULT false,
    flag_reason text,
    created_at  timestamptz DEFAULT now()
);

-- Index for parent reporting queries (owner + date range)
CREATE INDEX IF NOT EXISTS idx_query_log_owner_created
    ON public.query_log (owner, created_at DESC);

-- Index for flagged event review
CREATE INDEX IF NOT EXISTS idx_query_log_flagged
    ON public.query_log (flagged, created_at DESC)
    WHERE flagged = true;

-- Auto-purge: rows older than 30 days for non-flagged entries.
-- Run this as a Supabase scheduled function (cron: '0 2 * * *').
-- Flagged rows are retained indefinitely.
--
-- DELETE FROM public.query_log
-- WHERE flagged = false
--   AND created_at < now() - interval '30 days';
