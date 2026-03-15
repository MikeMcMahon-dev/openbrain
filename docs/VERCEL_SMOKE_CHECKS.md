# Vercel Smoke Checks (Pre-Production + Demo)

Use this file as the final checklist before any family-facing demo handoff.

---

## Rollout model

- `phase 1`: Vercel is wired to Supabase for primary reads/writes with legacy Chroma retained for reference only.
- `phase 2`: RLS + strict tenancy auth mapping is added after identity plumbing is complete.

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

### 3) Corpus and Tenant Handling

- Manual Obsidian re-import can be run and produces deterministic `source_chunk_id` values.
- Re-import does not create duplicate spikes for unchanged files.
- Owner/tenant context is consistently resolved from request headers.

### 4) Query/Generation behavior

- `POST /query`, `/api/query` return valid tutor payload.
- `POST /search`, `/api/search` return valid result envelope.
- `POST /generate_quiz`, `/api/generate_quiz` works.
- `POST /generate_flashcards`, `/api/generate_flashcards` works.
- `GET` responses remain method-restricted (`405`) where not supported.

### 5) Vercel App Surface

- `GET /` loads the web UI.
- Core UI actions hit header-scoped API endpoints.
- Legacy paths remain routed and functionally equivalent to `/api/*` versions:
  - `/query`, `/search`, `/generate_quiz`, `/generate_flashcards`, `/ingest`.

### 6) Error and Recovery

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
- No cross-tenant leakage in representative demo paths.
- Manual import path documented and successfully tested.

---

## Required preflight

Run first locally:

```bash
make smoke
```

Then run against deploy:

```bash
make smoke-live SMOKE_URL=https://openbrain-rouge.vercel.app
```

Then run:

```bash
make check
```

## Sign-off

Primary reviewer confirms:

- ingestion health
- context scoping
- query stability
- generation output
- rollback readiness
