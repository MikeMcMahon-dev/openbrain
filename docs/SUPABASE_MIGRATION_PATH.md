# Supabase Migration Path (Current State)

## Migration run protocol (required)

- Before every migration apply, run:
  - `supabase db push --linked --dry-run`
- Only after a clean dry-run, apply with:
  - `supabase db push --linked`
- Use `--include-all` only if you are intentionally reconciling migration history drift.

## What is already complete

The migration has moved to Supabase-first storage and retrieval readiness:

- `supabase/functions/ingest-thought/index.ts` writes Slack messages into `thoughts` with embeddings, metadata, and captured usernames.
- migration `20260314193123_add_slack_username.sql` added Slack attribution fields.
- migration `20260315193000_supabase_primary_schema_and_tenancy.sql` added tenant/source metadata, deterministic import keys, and search indexes.
- migration `20260315195000_add_user_identity_tenancy_fields.sql` added `open_brain_users`, `open_brain_tenants`, `open_brain_tenant_memberships` and added durable identity columns to `thoughts`.
- migration `20260315200000_enable_thoughts_rls.sql` added tenant-aware RLS helpers and policy scaffolding for authenticated access.
- signature verification and startup guards for Slack callbacks are active.
- migration application state is in sync between local files and remote project for `edljijurbmcupawnjpfx`.

Current canonical storage now is:

- `SUPABASE -> public.thoughts`
- `ChromaDB -> legacy/local` only

## Groundwork completed for multi-user and tenancy

The schema now includes tenancy-oriented fields intended for policy enforcement:

- `tenant_id`
- `visibility`
- `source_type`
- `source_team_id`, `source_workspace_id`, `source_channel_id`
- `created_by_user_id`, `created_by_user_login`
- `slack_user_id`
- `open_brain_users` / `open_brain_tenants` / `open_brain_tenant_memberships`
- deterministic identifiers: `document_id`, `chunk_id`, `source_chunk_id`
- optional `content_hash` for dedupe/replay-safe imports

Indexing added for:

- tenant-scoped queries
- ownership lookups
- deterministic source chunk de-duplication
- pgvector retrieval (`ivfflat` when supported by environment, with fallback handling)
- PostgreSQL full-text search (`GIN` on `to_tsvector`)

Recent migration outcome:

- 20260315195000 and 20260315200000 are now applied remotely.
- ANN index creation now avoids hard-fail on environments lacking `vector_cosine_ops`.

## What remains before production query cutover

- Add Vercel auth + tenancy context in API layer and switch writes/reads to user context values.
- Ensure Vercel/auth JWT values map to `open_brain_users` (`supabase_user_id`, `email`, or `slack_user_id`) before harden-gating `tenant_id`.
- Re-import Obsidian markdown into Supabase with deterministic IDs (overwrite-safe/idempotent).
- Stand up query parity checks (recall/latency) between legacy + Supabase for representative prompts.

## Suggested rollout

1. Keep Chroma reads disabled by default, but available as a debug fallback.
2. Deliver Vercel read path using Supabase source with `visibility` and `tenant_id` filters.
3. Run Vercel smoke checks in [docs/VERCEL_SMOKE_CHECKS.md](/Users/mmcmahon/src/home-lab/open-brain/docs/VERCEL_SMOKE_CHECKS.md).
4. Verify ingestion + query stability for family demo flows.
5. Enable formal RLS policy layer after auth + tenancy wiring is in place.
6. Retire legacy dependency only after accepted stability gates are met.

## Notes

- This is now a storage-direction migration rather than a dual-write prototype.
- Chroma remains useful for local experimentation but is no longer the production source of truth.
