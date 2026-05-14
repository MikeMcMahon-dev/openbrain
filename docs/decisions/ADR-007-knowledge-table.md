# ADR-007: Unified `knowledge` Table Replacing `thoughts`

**Status:** Accepted
**Date:** 2026-05-13

## Context

`public.thoughts` stores all knowledge as equal atoms in a vector space. Taxonomy is stored
inconsistently in a `metadata` JSONB field rather than as queryable columns — `subject` and
`topic` are not direct columns, making SQL filtering on domain or ownership awkward.

The table has no concept of domain separation (study notes bleed into ops queries), no
temporal lifecycle (stale state competes equally with current state), and no ownership scoping
beyond application-layer filtering.

A naive solution would add sub-tables per domain (one for Network state, one for Study, etc.).
This fails as the knowledge graph grows: tools must know which table to query, migrations
multiply, and there is no single query interface.

## Decision

Introduce a single `public.knowledge` table as the target for all new writes. It replaces
`public.thoughts` for new content while `thoughts` is preserved read-only during migration
validation.

Key structural differences from `thoughts`:

| Concern | `thoughts` | `knowledge` |
|---|---|---|
| Taxonomy | `metadata->>'subject'`, `metadata->>'topic'` (JSONB) | Direct columns: `domain`, `environment`, `system`, `tags` |
| Lifecycle | None — all records equally present | `status`, `valid_from`, `valid_until`, `supersedes_id` |
| Owner | `metadata->>'owner'` or `created_by_user_login` | `created_by` (direct column, matches token owner map) |
| Idempotency key | `source_chunk_id` or `content_hash` | `ingest_id` (direct column) |

The `thoughts` table is **not dropped** during migration. It is preserved as read-only, then
renamed to `thoughts_archive` after human sign-off on migration validation.

Existing `/openbrain_*` and `/claude_*` endpoints continue querying `thoughts` during the
migration window. New endpoints target `knowledge`. After validation and explicit human
approval, legacy endpoints are updated to target `knowledge`.

## Consequences

- Single query interface regardless of knowledge type — no tool needs to know which table
- Domain/environment/system are SQL-filterable without JSONB operators
- `tags` (TEXT[] with GIN index) supports flexible multi-dimensional filtering
- Migration required for all existing content — see `docs/migrations/001_domain_discovery.md`
- Migrated records use `status='historical'`; human review required to promote to `current`
- `thoughts` read-only period allows rollback if migration validation fails
- `ingest_id` replaces the inconsistent `source_chunk_id`/`content_hash` dual-key pattern
