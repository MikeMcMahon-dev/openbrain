# OpenBrain Next Steps

This document tracks planned improvements for the OpenBrain system.

---

## Last Successful Checks (2026-03-15)

- Deployment smoke: `https://openbrain-rouge.vercel.app`  
  - Local smoke checks passed
  - Live smoke checks passed
- Ingest pre-flight:
  - CLI ingest and API `/api/ingest` preflight summary now implemented
  - Idempotency path validated (before/after counts stable)
- Git:
  - Latest pushed commit: `21986df`
  - Branch: `main`

---

## Step 1 – Supabase as Primary Storage (Complete)

Primary storage now targets Supabase with pgvector:

- `thoughts` is the canonical source for Slack capture
- `openai/text-embedding-3-small` is the single embedding model for all canonical writes
- ChromaDB references are retired from runtime; Supabase is canonical.

---

## Step 2 – Retrieval and Tutor Baseline (Complete)

Keep existing retrieval capabilities while routing to Supabase source:

- retrieval API contracts are now exercised through Vercel serverless handlers in `api/`
- tutor endpoints (`/query`, `/generate_quiz`, `/generate_flashcards`) are live on both legacy and `/api` routes
- keyword + vector hybrid ranking remains active in current query path.

---

## Step 3 – Tenant & Ownership Groundwork (Complete)

Stand up schema-level tenancy before Vercel rollout:

- tenant and durable identity fields have been added for row ownership and isolation.
- shared/private/public intent fields and deterministic source IDs are in place for policy + re-import idempotence.
- tenancy tables and RLS scaffolding are in place and migration-applied.

Files:

- `20260315193000_supabase_primary_schema_and_tenancy.sql`
- `20260315195000_add_user_identity_tenancy_fields.sql`
- `20260315200000_enable_thoughts_rls.sql`

Planned follow-up:

- validate and enforce tenant-aware auth context mapping (`supabase_user_id`, `email`, or `slack_user_id`)
- add tenancy-aware query filters in API handlers (deployed)

---

## Step 4 – Slack Ingestion (Complete)

Slack message ingestion now works end-to-end in Supabase:

- `supabase/functions/ingest-thought/index.ts` receives events
- signature verification is enforced for Slack callbacks
- startup guards block slash commands and non-user message events
- `slack_username` is captured and persisted
- `slack_user_id` and tenant metadata are now written on ingest
- inserts to `thoughts` table are confirmed
- function returns confirmation back in the Slack thread

Current operational state:

- Project: `edljijurbmcupawnjpfx`
- Function endpoint:
  - `https://edljijurbmcupawnjpfx.supabase.co/functions/v1/ingest-thought`
- Migration artifacts added:
  - `20260314193123_add_slack_username.sql`
  - `20260315193000_supabase_primary_schema_and_tenancy.sql`
  - `20260315195000_add_user_identity_tenancy_fields.sql`
  - `20260315200000_enable_thoughts_rls.sql`

---

## Step 5 – MCP Layer Design

Target endpoints to expose:

- `POST /ingest`
- `POST /query`
- `POST /generate_quiz`
- `POST /generate_flashcards`

Detailed contracts are tracked in:

- `docs/MCP_CONTRACT.md`
- `docs/API_CONTRACT_EXAMPLES.md`
- `docs/CHUNK_METADATA_MODEL.md`
- `docs/OWNER_TENANCY_NOTES.md`
- `docs/TUTOR_BEHAVIOR_CONTRACT.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `docs/SUPABASE_MIGRATION_PATH.md`

Current status:

- API routes are scaffolded in `brain_server/server.py`.
- Ingestion orchestration through MCP transport is next after tenancy and Supabase read integration.
- Decision log tracked in `docs/MCP_DECISION_LOG.md`
  for unresolved implementation questions.

---

## Step 6 – Vercel App Implementation (Complete, hardening ongoing)

- web interface for thought capture, query, and generation is deployed at `https://openbrain-rouge.vercel.app/`
- app routes now support both legacy and `/api` endpoints
- tenancy context is resolved from request headers (`x-openbrain-owner`, `x-openbrain-tenant-id`) for user separation
- next work: secure auth binding and production-grade tenancy enforcement

---

## Step 7 – Vercel Rollout + Obsidian Backfill (Execution)

Before defaulting reads to Supabase:

- Re-import Obsidian markdown corpus into Supabase with deterministic `source_chunk_id`
- Verify row/document counts and coverage against baseline
- Run rollout smoke checks in `docs/VERCEL_SMOKE_CHECKS.md`
- Proceed to manual filesystem import test as your next operational validation
- Gate default source flip on parity results and rollback readiness

## Step 8 – Agent Communication Layer (In Progress)

- Build an **agent-agnostic communication layer** first, then map specific platform
  adapters (ChatGPT/custom GPT, Claude, etc.) on top.

- Publish explicit OpenBrain tool contracts for ChatGPT/custom GPT actions:
  - `openbrain_query`
  - `openbrain_generate_quiz`
  - `openbrain_generate_flashcards`
  - `openbrain_ingest`
- Execution details are captured in `docs/CHATGPT_CONNECTOR.md`.
- Keep `api/chatgpt.py` as a light adapter for now, but route it through a shared
  core protocol so we can swap in other agent runtimes without changing API
  semantics.
- Add identity bridge (chat user → owner/tenant headers) to prevent users writing
  as another identity.
- Add response shape guards so the tool call returns deterministic fields and
  meaningful validation errors.
- Validate with a manual chat flow:
  - ask a study question
  - request flashcards
  - request quiz
  - confirm that output references imported family notes.
- Add request signing / shared secret validation for third-party agents (not just
  ChatGPT) and rate-limit handling around `/api/ingest` and query calls.

- Deliverable target: stable Custom GPT action configuration + working family
  smoke checks for chat tool routes.

## Night-to-Morning Handoff (Next Session)

What was done:

- Ingestion CLI and Vercel `/api/ingest` now share pre-flight guardrails:
  - owner/tenant checks
  - schema readiness checks
  - source existence checks
  - existing row count summary for transparency
  - blocked-on-failure behavior for hard issues
- End-to-end smoke checks are passing and deployed API returns clean responses at
  `https://openbrain-rouge.vercel.app`.
- Idempotency checks for repeated ingests are in place and automated by
  `scripts/smoke_checks.py --idempotency-source ...`.

Immediate next actions:

- Run a manual one-shot ingest from your chosen Obsidian source and confirm:
  - response includes `preflight.existing_rows`
  - `preflight.status` is `ok` when expected
  - re-running same source does not increase row count
- Validate manual import flow from UI one more time for family UX.
- Move into chat connector work:
  - define the user/tenant identity bridge for each request
  - align Custom GPT action payloads to API response schema in `docs/CHATGPT_CONNECTOR.md`
- After connector validation, run a final `make smoke-live SMOKE_URL=https://openbrain-rouge.vercel.app`.
- Confirm `api/chatgpt.py` is intentionally a thin platform adapter (not the source of truth) and that new providers can reuse the same shared contract with minimal code.
- Add "agent handoff hardening" checklist before rollout:
  - preflight status surfaced in UI/API responses
  - clear owner/tenant validation errors
  - explicit idempotency behavior for repeated content pushes
  - rollback note for failed batch imports.

---

## Future Considerations

- Add richer hybrid ranking and re-rankers in production query path.
- Move metadata routing into shared retrieval abstractions.
- Remove legacy-only assumptions once Vercel + Supabase parity is proven.

---

## Long-Term Direction

OpenBrain becomes the family-facing, multi-user knowledge memory with:

- material ingestion (notes, PDFs, DOCX, URLs)
- retrieval and tutor support
- strong tenancy and privacy boundaries
- production-friendly maintenance and operations
