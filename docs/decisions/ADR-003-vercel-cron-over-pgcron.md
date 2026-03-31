# ADR-003: Vercel Cron Jobs over pg_cron for Session Reports

**Status:** Accepted (supersedes pg_cron approach)
**Date:** 2026-03-29

## Context
Nightly session reports needed a scheduler. Initial plan used Supabase pg_cron extension. Migration 002 was written for pg_cron.

## Decision
Vercel Cron Jobs replace pg_cron entirely. Schedule defined in vercel.json. Handler at GET /api/cron/session_report. Auth via CRON_SECRET (auto-managed by Vercel).

## Consequences
- pg_cron extension not enabled by default in Supabase — would require manual activation per project
- Scheduling belongs with the app layer, not the database layer
- Migration 002 deprecated — kept for reference but no SQL required
- CRON_SECRET is auto-injected by Vercel at runtime — not visible in env vars UI, not user-managed
- Cron fires at 03:00 UTC (9pm MDT) — uses `yesterday` UTC date for data lookup
- REPORT_CONFIGS env var: JSON array of {owner, recipients} — update in Vercel dashboard, no redeploy needed
- Cron only active on production deployment (main branch) — preview deploys do not fire cron
