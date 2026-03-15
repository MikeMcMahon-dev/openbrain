# Owner / Tenant Handling Notes

## Why `owner` matters now

- `owner` is the tenant namespace for all student-specific context.
- Search and keyword fallback both gate by owner when present, isolating
  each user's study corpus.
- This prevents cross-user retrieval noise before full identity plumbing
  exists.

## MCP `/ingest` owner rule

- `owner` should always be resolved in this precedence order:
  - request context header (`x-openbrain-owner`, `x-openbrain-user-login`, `x-openbrain-user-id`, `x-slack-user-id`, `x-user-id`)
  - configured default from deployment env (`OPENBRAIN_DEFAULT_OWNER`)
  - fallback environment/default `OPENBRAIN_DEFAULT_OWNER` (`mmcmahon`)
- Stored per chunk as metadata so retrieval and future re-query are
  owner-aware.
- Owner is also included in deterministic ingest id generation for
  idempotent retries.

## MCP `/query` owner rule

## MCP `/query`, `/search`, `/generate_*`

- `/query`, `/search`, `/generate_quiz`, and `/generate_flashcards` now
  resolve owner/tenant from request context headers first.
- Explicit `owner` / `tenant_id` in request body is currently treated as
  legacy input and is ignored for scope resolution unless a future
  allowlist enables client override.
- Query behavior uses tenant-filtered retrieval so the response set stays
  within the resolved tenant/context.

## User-specific metadata strategy

- Keep owner as a mandatory metadata field in future ingestion
  contracts.
- Treat `user_id` as a potential second identifier only if auth
  integration adds strong identity.
- Avoid using free-text owner values in automated ingestion scripts
  unless validated upstream.

## MCP step-by-step walkthrough

1. Client submits `/ingest` with source payload and owner.
2. Endpoint validates payload and sets status:
   - `accepted` for valid obsidian payloads
   - `queued` for non-obsidian payloads awaiting orchestration
   - `failed` with details when payload is invalid
3. Subject/topic fallback is applied if omitted:
   - `subject` from source filename
   - `topic` as date fallback (`YYYY-MM-DD`)
4. Ingest orchestration hook is scaffolded, not yet asynchronous job-backed.
5. Ingest returns deterministic `ingest_id` for dedupe-safe retries.
6. Client submits `/query` with same owner.
7. Server builds embedding and calls vector query with owner filter.
8. Keyword hits are surfaced first, with vector matches filling remaining
  slots when needed.
9. Tutor module receives context + query and returns mode-specific guidance.
