# Supabase Migration Path (No Immediate Commit)

## Why this is a later-phase task

- Current architecture is local-first and already works with Chroma.
- Migration should happen after vector and ingestion behavior are stable in
  multi-source tests.
- Current goal is not broad multi-user support yet, so local vector store
  remains optimal for speed.

## Current state snapshot

- Storage: `brain_index/` (local Chroma persistent directory)
- Collection: `openbrain`
- Vector engine: Chroma + local HNSW/index state in sqlite + binary files
- Embeddings: `BAAI/bge-small-en`
- Metadata used: `source`, `file`, `section`, `heading`, `chunk`,
  `owner`, plus planned `content_type`, `subject`, `topic`

## Target Supabase state

- Managed Postgres with pgvector extension
- Table design candidate:
  - `id` UUID
  - `document_id` text (compatibility with current chunk ids)
  - `vector` vector(384 or model-compatible dims)
  - `text` text
  - `metadata` jsonb
  - `tenant_owner` text
  - `content_type` text
  - `subject` text
- `tenant_owner` maps from current `owner`.

## Migration staging approach

- Keep Chroma as source of truth during validation.
- Add an export layer:
  - read all rows from Chroma collection
  - map metadata to `metadata` jsonb and normalized columns
  - write vectors to pgvector table
- Build dual-write mode temporarily:
  - new ingests write to Chroma and Supabase in parallel
  - reads compare vector deltas until parity is validated
- Cutover:
  - `/query` search path switches from Chroma to Supabase
  - rollback path remains by re-pointing collection provider only

## Risk and effort estimate

- Moderate effort with careful parity testing.
- Main work: schema migration, index build, query fallback semantics,
  auth context threading, and deployment credentials.
- Lower-risk if performed in phases:
  - schema + writer migration
  - side-by-side retrieval validation
  - production read cutover
  - observability and cleanup

## Compatibility rules during migration

- Do not change:
  - chunking behavior yet
  - embedding model
  - `owner`-first filtering semantics
- Preserve existing metadata keys to avoid tutor/retrieval code churn.
