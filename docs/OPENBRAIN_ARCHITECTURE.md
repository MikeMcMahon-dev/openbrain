# OpenBrain Architecture

OpenBrain is now Supabase-first for persistence and retrieval.

- Primary web path: Vercel + Supabase
- Primary database: Supabase Postgres with pgvector
- Legacy/local reference path: ChromaDB (`brain_index/`) and local ingestion scripts

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

## 2. Legacy Local Ingestion Pipeline

`scripts/ingest.py`

Current legacy responsibility:

- reading markdown files
- heading-based chunking
- filtering small chunks
- local experimental embeddings/chunks
- local Chroma vector persistence for historical compatibility

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

`openai/text-embedding-3-small`

This is now the standard across Slack ingest and Obsidian/DB import paths to keep vector dimensions consistent.

---

## 5. FastAPI / API Layer

`brain_server/server.py`

Provides:

- MCP-like routes in scaffold:
  - `POST /ingest`
  - `POST /query`
  - `POST /generate_quiz`
  - `POST /generate_flashcards`

The current focus is the Vercel web surface, with MCP contracts tracking the API evolution.

---

## 6. Legacy Local Indexing

Uses:

- watchdog

Current intent:

`vault change -> debounce timer -> chunk/index -> legacy vector upsert`

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
- Slack ingestion is stable in production
- Vercel app implementation is in-progress

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

## Long-Term Direction

OpenBrain will support:

- AI agents querying the knowledge base
- automated documentation retrieval
- engineering memory persistence
- code assistant integration
