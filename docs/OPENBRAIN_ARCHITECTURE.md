# OpenBrain Architecture

OpenBrain is now Supabase-first for persistence and retrieval.

- Primary web path: Vercel + Supabase
- Primary database: Supabase Postgres with pgvector
- Local scripts are retained for source processing and experimentation; production reads/writes flow through Supabase only.

It functions as a **personal and family RAG engine** with a unified web surface.

---

## Core Components

## 1. Obsidian Vault

Primary source knowledge base for note ingestion.

Contents:
- infrastructure notes
- automation documentation
- study materials
- homelab design decisions

---

## 2. Ingestion Pipeline

`scripts/ingest.py`

Current responsibility:

- reading markdown files
- heading-based chunking
- filtering small chunks
- writing chunks/embeddings to Supabase `public.thoughts`

Automated ingest pre-flight safeguards:
- Validates required fields for safe writes:
  - owner and tenant values are present
  - expected `thoughts` columns exist for tenancy (`tenant_id`, `created_by_user_login`, `embedding`, etc.)
  - source reachability
- Reports existing row count for each source+owner+tenant before writes so duplicate risk is explicit.
- Enforces embedding compatibility: missing embedding keys or dimension mismatches block write.
- Fails fast before mutating DB when environment or schema prerequisites are not met.

---

## 3. Primary Vector Database

`Supabase Postgres (pgvector)`

Stores:

- embeddings
- chunk text
- metadata JSON and tenancy fields

Primary metadata fields now tracked in DB:

- `tenant_id`
- `document_id`
- `chunk_id`
- `source_type`
- `created_by_user_id`
- `slack_username`
- `visibility`
- `slack_user_id`
- `open_brain_users` for durable identity mapping
- `open_brain_tenants` + `open_brain_tenant_memberships` for RBAC boundaries

---

## 4. Embedding Model (Primary path)

Model: `text-embedding-3-small` (1536 dimensions)
Provider: **OpenRouter** (`OPENROUTER_API_KEY` + `OPENROUTER_BASE_URL=https://openrouter.ai/api/v1`)

This is the standard across Slack ingest and Obsidian/DB import paths to keep vector dimensions consistent.
No direct OpenAI key is required — all embedding calls route through OpenRouter.

---

## 5. Vercel API Layer

`api/app.py`, `api/index.py`, and route handlers in `api/*.py`

Provides:

- Core routes: `POST /ingest`, `/query`, `/search`, `/generate_quiz`, `/generate_flashcards` (and `/api/*` variants)
- Tool routes: `/openbrain_query`, `/openbrain_generate_quiz`, `/openbrain_generate_flashcards`, `/openbrain_ingest` (+ `/tools/*` variants)
- Claude routes: `/claude_query`, `/claude_generate_quiz`, `/claude_generate_flashcards`, `/claude_ingest`

The primary web surface is Vercel, with these API contracts now exercised through
the deployed serverless entrypoint.

The ingest endpoints (`/ingest`, `/api/ingest`) now return a `preflight` object:
- `preflight.status`: `ok` / `failed`
- `preflight.warnings`: non-blocking notes (for example local fallback embedding)
- `preflight.errors`: blocking issues (owner, tenant, DB URL, schema checks)
- `preflight.existing_rows`: count when available, to make re-ingest behavior transparent

Runtime credential sourcing:
- **Local CLI** (`python ./scripts/ingest.py`) reads `.env.local` / `.env` from repo root.
- **Vercel deployment** reads environment variables configured in Vercel project settings.
- Local `.env.local` is created/synced by `vercel env pull` — keep it in sync when adding Vercel env vars.
- New Vercel env vars require a **redeploy** to take effect in the Python serverless runtime.
- **IPv4 requirement**: Vercel serverless is IPv4-only. Use the Supabase **Transaction Pooler**
  connection string (port `6543`, host `aws-0-us-east-1.pooler.supabase.com`) for `SUPABASE_DB_URL`.
  The direct DB URL and session pooler may resolve to IPv6 and will fail in Vercel.

---

## 6. Ingestion Triggering

Current intent:

`vault change -> chunk/index -> local toolchain -> Supabase upsert`

Pre-flight rule:
- One-click imports should not proceed when `preflight.status` is `failed`, and repeated imports should preserve source idempotency via deterministic IDs.

---

## 7. Slack Capture + Supabase Ingestion (Deployed)

`supabase/functions/ingest-thought/index.ts`

Responsibilities:

- verifying Slack requests with signing secret
- filtering to user-authored message events
- enriching metadata with user identity
- extracting and storing embeddings
- inserting rows into `thoughts` table
- posting confirmation responses back in Slack thread

Data captured:

- `content`
- `metadata` (type, topics, people, action items, source, slack_ts)
- `embedding`
- `slack_username`
- `slack_user_id`, tenant context, and source metadata added via migration
- user identity upsert for durable `open_brain_users` rows on each capture

Supporting migration artifacts:
- `/Users/mmcmahon/supabase/migrations/20260314193123_add_slack_username.sql`
- `/Users/mmcmahon/supabase/migrations/20260315193000_supabase_primary_schema_and_tenancy.sql`
- `/Users/mmcmahon/supabase/migrations/20260315195000_add_user_identity_tenancy_fields.sql`
- `/Users/mmcmahon/supabase/migrations/20260315200000_enable_thoughts_rls.sql`

---

## 8. Vercel Web Interface (Primary surface)

Primary web entrypoint for:
- thought capture and review
- semantic + keyword-search retrieval
- user controls for channel/channel-scoped settings and tenancy boundaries

Status:
- Slack ingestion is stable in production.
- Vercel app implementation is live and smoke-validated at:
  - `/`
  - `/query`, `/api/query`
  - `/search`, `/api/search`
  - `/generate_quiz`, `/api/generate_quiz`
  - `/generate_flashcards`, `/api/generate_flashcards`
  - `/ingest`, `/api/ingest`

---

## 9. Retrieval Strategy

Search ranking combines:

- vector similarity from pgvector
- full-text search (keyword) path for precision

This hybrid strategy is the preferred direction for technical content.

Example:

query: `terraform modules`

- semantic: nearby automation concepts
- keyword: exact terraform references

---

## 10. System Design Goals

OpenBrain prioritizes:

- web-first multi-user usability
- consistent embeddings across all writers
- secure tenancy boundaries
- extensible query surfaces

---

## 11. ChatGPT + Claude Integration Layer (Complete)

### Custom GPT (ChatGPT)

Three family Custom GPTs are live, one per user, each with an isolated bearer token:

- OpenAPI 3.1.0 spec: `docs/CUSTOM_GPT_ACTION_SPEC.yaml`
- System prompts: `docs/gpt_instructions/` (mike_mcmahon67, snapple01, anneliesepaige)
- Routes: `/openbrain_query`, `/openbrain_generate_quiz`, `/openbrain_generate_flashcards`, `/openbrain_ingest`
- Auth: `Authorization: Bearer <per-user-token>`; token resolves to owner via `OPENBRAIN_TOKEN_OWNER_MAP`

### Claude Code MCP Server

`mcp_server/openbrain.py` — stdio MCP server registered via `.mcp.json`. Exposes four native tools directly in Claude Code sessions: `openbrain_query`, `openbrain_ingest`, `openbrain_generate_quiz`, `openbrain_generate_flashcards`.

### Identity Bridge

`OPENBRAIN_TOKEN_OWNER_MAP` (Vercel env var) — JSON mapping bearer token → owner string. Each family member has an isolated token. Token resolution happens in `api/chatgpt.py:_require_tool_auth()` and injects `x-openbrain-owner` before core logic runs. No code changes needed in `_openbrain_api.py`.

### Claude API adapter

`api/claude.py` — thin adapter for Claude native `tool_use` format (prioritises `input` key). Shares `_require_tool_auth` and `_inject_token_owner` with `api/chatgpt.py`. Routes: `/claude_query`, `/claude_generate_quiz`, `/claude_generate_flashcards`, `/claude_ingest`.

---

## Long-Term Direction

OpenBrain will support:

- AI agents querying the knowledge base
- automated documentation retrieval
- engineering memory persistence
- code assistant integration
