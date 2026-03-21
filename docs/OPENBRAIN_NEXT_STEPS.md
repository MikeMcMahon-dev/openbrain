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
- Session handoff: use [docs/HANDOFF.md](docs/HANDOFF.md) for next-session startup context.

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

## Step 8 – Agent Communication Layer (Partially Complete)

### What is done
- `api/_openbrain_api.py` is the agent-agnostic core: query, search, ingest, hybrid
  retrieval, preflight, tutor packet generation.
- `api/chatgpt.py` is a thin adapter (~108 lines): handles `tool_input` / `input` /
  `arguments` payload envelope styles, delegates entirely to `_openbrain_api`.
- Routes wired and deployed in `vercel.json` + `api/app.py`:
  - `/openbrain_query`, `/tools/openbrain_query`
  - `/openbrain_generate_quiz`, `/tools/openbrain_generate_quiz`
  - `/openbrain_generate_flashcards`, `/tools/openbrain_generate_flashcards`
  - `/openbrain_ingest`, `/tools/openbrain_ingest`
- `OPENBRAIN_TOOL_ACCESS_TOKEN` shared secret gate is implemented and active in Vercel.
  Wrong token → 401. Correct token required in `Authorization: Bearer <token>` or
  `X-OpenBrain-Tool-Token: <token>`.

### Current blocker
- **Vercel read path returns empty results** due to IPv6/IPv4 DB connection failure.
  Fix: update `SUPABASE_DB_URL` to Transaction pooler URL (port 6543). See HANDOFF.md.

### Still to do
- Fix DB connection in Vercel (unblocks everything else).
- Write Custom GPT action OpenAPI spec and configure in ChatGPT.
- Validate with a manual chat flow:
  - ask a study question → request flashcards → request quiz
  - confirm output references imported vault notes
- Cross-tenant leak test (different owner headers must not bleed results).
- Identity bridge: Slack user_id as canonical identity; per-user token → owner
  mapping for multi-user rollout (post-MVP).

### Design constraints preserved
- `api/chatgpt.py` stays a thin platform adapter — all logic stays in `_openbrain_api.py`.
- Owner/tenant resolved from request headers, not request body.
- `ingest_id` is deterministic (md5) for idempotent retries.

## Session Handoff (2026-03-20)

What was done this session:

- Confirmed 775 rows in Supabase with embeddings; corpus import validated.
- `OPENBRAIN_TOOL_ACCESS_TOKEN` generated and set in Vercel; auth gate confirmed working
  (wrong token → 401).
- Identified and documented root cause of empty Vercel read results: IPv6 DB URL
  incompatible with Vercel's IPv4-only serverless runtime.
- Corrected architecture docs: embeddings use OpenRouter, not direct OpenAI.
- Updated HANDOFF.md, ARCHITECTURE.md, NEXT_STEPS.md to reflect actual state.

Immediate next actions (start here):

1. Get Transaction pooler connection string from Supabase (Settings → Database →
   Connection string → Transaction mode, port 6543).
2. Update `SUPABASE_DB_URL` in Vercel env and in `.env.local`.
3. Redeploy Vercel, then run: `make smoke-live SMOKE_URL=https://openbrain-rouge.vercel.app`
4. Confirm live query returns vault content (not empty results).
5. Write Custom GPT action OpenAPI spec (see `docs/CHATGPT_CONNECTOR.md` for tool shapes).
6. Configure Custom GPT action, run end-to-end study flow, cross-tenant leak test.

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
