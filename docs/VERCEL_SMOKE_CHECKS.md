# Vercel Smoke Checks (Pre-Production + Demo)

Purpose: verify safety and behavior before exposing the Vercel interface.

---

## Rollout model

- `phase 1`: Vercel is wired to Supabase for primary reads/writes with legacy Chroma retained for reference only.
- `phase 2`: RLS + tenancy policy enforcement is enabled before user-facing defaults are flipped.

Current status:
- `phase 1` checks are green in smoke testing.
- `phase 2` work remains for policy hardening and user-context mapping.

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
- `/api/ingest` validates response payload:
  - `ingest_id`
  - `status` (`accepted` or `queued`)
  - `source_type`
  - `source`
  - `owner`
  - `subject`
  - `topic`
  - `message`
  - `details`

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

## Operational preflight (every deploy, local first)

Run locally:

```bash
make smoke
```

Run the same checks against the deployment URL:

```bash
make smoke-live SMOKE_URL=https://<project-domain>.vercel.app
```

Then run:

```bash
make check
```

before marking a release or demo-ready.

Current command for this project:

```bash
make smoke-live SMOKE_URL=https://openbrain-rouge.vercel.app
```

## Sign-off

Primary reviewer confirms:

- ingestion health
- corpus parity
- query stability
- error handling
- rollback behavior
