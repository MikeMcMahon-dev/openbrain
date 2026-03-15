# Vercel Smoke Checks (Pre-Production + Demo)

Purpose: verify safety and behavior before exposing the Vercel interface.

---

## Rollout model

- `phase 1`: deploy Vercel with Supabase as primary read path and optional Chroma legacy fallback for emergency comparison.
- `phase 2`: enable RLS + tenancy policy enforcement and lock Chroma to legacy-only.

This pattern keeps risk bounded while preserving rollback options early in rollout.

---

## Smoke Check Categories

### 1) Deployment and Connectivity

- Vercel deploy succeeds.
- Environment variables are present:
  - `SUPABASE_URL`
  - `SUPABASE_ANON_KEY`
  - `OPENBRAIN_QUERY_SOURCE` (or equivalent switch)
- Database connectivity check against Supabase succeeds.
- Required secrets are not exposed in build logs.

### 2) Ingestion Integrity

- Manual Slack message in capture channel creates expected DB row.
- `slack_username` is populated.
- Function posts Slack confirmation reply.
- `thoughts` row includes tenancy and source metadata fields.

### 3) Corpus Backfill Validation

- Obsidian markdown re-import is rerun for full corpus.
- Re-import produces deterministic IDs and matches expected counts.
- Re-import completes without duplicate-spike regressions.

### 4) Retrieval Parity and Ranking

- Sample queries return required payload fields.
- Hybrid path returns stable behavior between cached/local control queries and Supabase production path.
- Relevance regressions remain within agreed tolerance.

### 5) Vercel UI Behavior

- Thought list renders for seeded corpus.
- New query returns quickly and deterministically.
- Supabase-first feature flags behave as expected:
  - `supabase` mode works
  - optional `legacy` mode still returns for admin fallback only

### 6) Error and Recovery

- Corrupt/missing query input returns graceful error response.
- Supabase timeout/failure fails safe (clear UI message, no crash).
- Admin fallback to legacy path can be toggled when enabled.

---

## Phase 1 acceptance

- Supabase query path is functional and stable.
- Ingestion loop remains reliable in Slack.
- Legacy fallback behavior is available but not default.

## Phase 2 acceptance

- RLS + tenancy filters are active and tested.
- No unresolved critical errors for representative family-demo queries.
- Data ownership boundaries behave as expected.

## Sign-off

Primary reviewer confirms:

- ingestion health
- corpus parity
- query stability
- error handling
- rollback behavior
