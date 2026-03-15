# Owner / Tenant Handling Notes

## Why `owner` matters now

- `owner` is the tenant namespace for all student-specific context.
- Search and keyword fallback both gate by owner when present, isolating
  each user's study corpus.
- This prevents cross-user retrieval noise before full identity plumbing
  exists.

## MCP `/ingest` owner rule

- `owner` should always be resolved in this precedence order:
  - explicit `request.owner`
  - configured default from deployment env (`OPENBRAIN_DEFAULT_OWNER`)
  - fallback environment/default `mmcmahon` (`OPENBRAIN_DEFAULT_OWNER`)
- Stored per chunk as metadata so retrieval and future re-query are
  owner-aware.
- Owner is also included in deterministic ingest id generation for
  idempotent retries.

## MCP `/query` owner rule

- `/query`, `/generate_quiz`, `/generate_flashcards` read owner from
  request.
- If caller omits owner:
  - fallback to server default.
- If caller supplies owner, query path filters vectors using metadata
  owner first.
- If owner-filtered vector query errors, endpoint can fallback without
  owner filter (current behavior), but this is temporary.

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
