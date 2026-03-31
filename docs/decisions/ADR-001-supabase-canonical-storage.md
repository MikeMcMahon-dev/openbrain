# ADR-001: Supabase as Canonical Storage

**Status:** Accepted
**Date:** 2026-03-20

## Context
OpenBrain needed a vector store for RAG retrieval. Options considered: local ChromaDB, Pinecone, Weaviate, Supabase pgvector.

## Decision
Supabase (pgvector) is the canonical storage layer. All ingests write to `public.thoughts`. All retrieval reads from `public.thoughts`. The Supabase REST client is used for some operations but psycopg (direct Postgres connection) is required for any write that needs reliability — the REST client has proven unreliable for high-frequency writes.

## Consequences
- Direct Postgres URI required in SUPABASE_DB_URL (postgresql://) — not the REST URL (https://)
- Vercel serverless requires Transaction Pooler on port 6543, not 5432
- RLS scaffolded but not enforced (Phase 2) — tenant isolation currently via app-layer owner filtering
- pgvector handles both keyword and vector search — no separate vector store needed
- Silent failures: psycopg catches all exceptions with `except Exception: pass` in log_query — check query_log entries to confirm logging is working after deploys
