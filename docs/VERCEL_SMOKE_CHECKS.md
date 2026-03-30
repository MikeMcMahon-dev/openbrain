# Vercel Smoke Checks (Pre-Production + Demo)

Use this file as the final checklist before any family-facing demo handoff.

---

## Rollout model

- `phase 1` (complete): Vercel is wired to Supabase for primary reads/writes.
- `phase 2` (in progress): RLS policy hardening (scaffolded, not enforced).

Current status:
- `phase 1` checks are green in smoke testing.
- `phase 2` is in progress; API context resolution is now header-first.

---

## Smoke check categories

### 1) Deployment and Connectivity

- Vercel deployment is current on `main`.
- The URL responds and serves the demo page at `/`.
- Environment includes required values for this stack:
  - `SUPABASE_URL`
  - `SUPABASE_ANON_KEY` or compatible service key
  - embedding key for configured model path

### 2) Ingestion Integrity

- `/api/ingest` and `/ingest` return a valid response shape:
  - `ingest_id`
  - `status` (`accepted` or `queued`)
  - `source_type`
  - `source`
  - `owner`
  - `subject`
  - `topic`
  - `message`
  - `details`

- In Vercel, `/api/ingest` can validate and queue remote/local paths only if that path is reachable by the Vercel runtime.
  - Local filesystem paths from your laptop (for example `/tmp` or `/Users/...`) are not reachable from Vercel.
  - For local Obsidian imports, run the ingest command from the local machine or use a temporary public source plus bulk `sources` list.

### 3) Corpus and Tenant Handling

- Manual Obsidian re-import can be run and produces deterministic `source_chunk_id` values.
- Re-import does not create duplicate spikes for unchanged files.
- Owner/tenant context is consistently resolved from request headers.

### 4) Query/Generation behavior

- `POST /query`, `/api/query` return valid tutor payload. **Auth required** (Bearer token).
- `POST /search`, `/api/search` return valid result envelope. **Auth required** (Bearer token).
- `POST /generate_quiz`, `/api/generate_quiz` works.
- `POST /generate_flashcards`, `/api/generate_flashcards` works.
- `/openbrain_query`, `/openbrain_generate_quiz`, `/openbrain_generate_flashcards`, `/openbrain_ingest` are live and auth-gated (Bearer token required).
- `/claude_query`, `/claude_generate_quiz`, `/claude_generate_flashcards`, `/claude_ingest` are live (Claude tool_use envelope).
- `GET` responses remain method-restricted (`405`) where not supported.

### 5) Reporting

- `POST /session_report` — requires Bearer token; returns 400 (missing owner/recipients), 403 (cross-tenant), 200 (sent or skipped).
- `GET /api/cron/session_report` — auth via `CRON_SECRET`; returns 200 with results array or skipped status.
- `REPORT_CONFIGS` env var configured with owner + recipient list.
- Vercel cron job visible in dashboard → Cron Jobs tab after deploy to `main`.

### 6) Vercel App Surface

- `GET /` loads the web UI.
- Core UI actions hit header-scoped API endpoints.
- Legacy paths remain routed and functionally equivalent to `/api/*` versions:
  - `/query`, `/search`, `/generate_quiz`, `/generate_flashcards`, `/ingest`.

### 7) Error and Recovery

- Bad input returns a JSON error payload and non-200 status.
- 500 cases are visible in Vercel logs with clear traces.
- No serverless init-time runtime panics in smoke window.

---

## Acceptance gates

### Phase 1

- Supabase path is stable for read/write and ingest.
- Smoke checks run clean in staging/production URL.
- No regressions in `/api/health` and `/health`.

### Phase 2

- RLS migration objects applied.
- Header-driven tenant context validated in integration checks.
- Cross-tenant read prevention enforced via `TOKEN_OWNER_MAP` — validated in smoke (403 on mismatch). ✓
- Manual import path documented and successfully tested.

---

## Required preflight

Run first locally:

```bash
.venv/bin/python scripts/smoke_checks.py
```

Then run against deploy:

```bash
.venv/bin/python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app
```

## Sign-off

Primary reviewer confirms:

- ingestion health
- context scoping
- query stability
- generation output
- rollback readiness
