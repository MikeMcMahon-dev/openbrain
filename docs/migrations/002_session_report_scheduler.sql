-- Migration 002: nightly session report scheduler via Supabase pg_cron
-- Run in Supabase SQL Editor
-- Requires pg_cron extension (enabled by default on Supabase).
--
-- This fires nightly at 21:00 UTC (3pm MDT / 4pm MDT summer).
-- Calls the Vercel session_report endpoint for each configured child owner.
-- Skips silently if no queries were logged that day (handled by the endpoint).
--
-- Prerequisites:
--   1. RESEND_API_KEY set in Vercel environment variables
--   2. RESEND_FROM_EMAIL set in Vercel environment variables
--   3. OPENBRAIN_TOOL_ACCESS_TOKEN set (used as bearer token below)
--   4. 001_query_log.sql migration has been run
--
-- Replace the placeholder values before running:
--   <VERCEL_URL>        e.g. openbrain-rouge.vercel.app
--   <PARENT_API_TOKEN>  value of OPENBRAIN_TOOL_ACCESS_TOKEN
--   <PARENT_EMAIL>      where reports should be sent

SELECT cron.schedule(
  'annie-nightly-session-report',
  '0 21 * * *',
  $$
  SELECT net.http_post(
    url := 'https://<VERCEL_URL>/session_report',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer <PARENT_API_TOKEN>'
    ),
    body := jsonb_build_object(
      'report_type', 'study_session',
      'owner', 'annie',
      'recipients', jsonb_build_array('<PARENT_EMAIL>')
    )
  );
  $$
);
