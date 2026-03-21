# OpenBrain Next Steps

This document tracks planned improvements for the OpenBrain system.

---

## Last Successful Checks (2026-03-21)

- Deployment smoke: `https://openbrain-rouge.vercel.app`
  - Local smoke checks passed (all routes)
  - Live smoke checks passed (22/22 cases including 401 rejection)
- DB state: 519 rows, 3 clean owners (`mike.mcmahon67`, `snapple01`, `anneliesepaige`)
- MCP server live in Claude Code via `.mcp.json`
- Git:
  - Latest pushed commit: `83f0160`
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

## Step 5 – MCP Layer (Complete)

All target endpoints live and auth-gated on Vercel:

- `POST /openbrain_query` + `/tools/openbrain_query`
- `POST /openbrain_generate_quiz` + `/tools/openbrain_generate_quiz`
- `POST /openbrain_generate_flashcards` + `/tools/openbrain_generate_flashcards`
- `POST /openbrain_ingest` + `/tools/openbrain_ingest`
- `POST /claude_query`, `/claude_generate_quiz`, `/claude_generate_flashcards`, `/claude_ingest` (Claude tool_use envelope)

Claude Code MCP server: `mcp_server/openbrain.py` — registered via `.mcp.json`, exposes all four tools natively in Claude Code sessions.

Detailed contracts: `docs/MCP_CONTRACT.md`, `docs/API_CONTRACT_EXAMPLES.md`

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

## Step 8 – Agent Communication Layer (Complete)

### What is done
- `api/_openbrain_api.py` — agent-agnostic core: query, search, ingest, hybrid retrieval, preflight, tutor packet generation.
- `api/chatgpt.py` — thin adapter for ChatGPT tool_use envelope (`tool_input` / `input` / `arguments`). Handles per-user token → owner resolution via `OPENBRAIN_TOKEN_OWNER_MAP`.
- `api/claude.py` — thin adapter for Claude native `tool_use` format (`input` key).
- All routes live on Vercel, auth-gated, smoke-tested.
- `OPENBRAIN_TOKEN_OWNER_MAP` — JSON env var mapping per-user bearer tokens to owner strings. Three family members each have an isolated token and data scope.
- Custom GPT OpenAPI spec: `docs/CUSTOM_GPT_ACTION_SPEC.yaml` (OpenAPI 3.1.0)
- Three family Custom GPTs configured: Mike, Beth (snapple01), Annie (anneliesepaige)
- Text ingest (`source_type=text`) live and working. Word-count guard: 413 returned if payload exceeds `OPENBRAIN_TEXT_INGEST_MAX_WORDS` (default 6000).
- MCP server for Claude Code: `mcp_server/openbrain.py`

### Design constraints preserved
- `api/chatgpt.py` and `api/claude.py` stay thin platform adapters — all logic in `_openbrain_api.py`.
- Owner/tenant resolved from request headers, not request body.
- `ingest_id` is deterministic (md5) for idempotent retries.

## Session Handoff (2026-03-21)

What was done this session:

- Transaction pooler (port 6543) confirmed working end-to-end.
- Identity bridge deployed: per-user token → owner mapping for Mike, Beth, Annie.
- Tokens rotated after discovering prior tokens in git history.
- Custom GPT OpenAPI spec written and validated. Three family Custom GPTs created.
- Text ingest source_type fixed (was silently failing). Word-count guard added.
- Claude MCP adapter (`api/claude.py`) written and deployed.
- MCP server built and registered in Claude Code via `.mcp.json`.
- DB cleanup: 380 `default_user` duplicate rows deleted, 10 orphaned rows re-attributed.
- Git best practices doc written and ingested into brain.
- `gh` CLI installed.

Immediate next actions:
1. Annie's school content import (coordinate when she brings laptop post-Spring Break).
2. Ingest Custom GPT share URLs into brain once all three confirmed.
3. DB health automation — planned as K8s CronJob (separate project).

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
