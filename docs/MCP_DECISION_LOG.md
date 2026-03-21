# MCP and Tutor Decision Log

This log captures decisions that currently block deeper implementation and
should be confirmed before code changes.

## Decisions already made

- Keep Supabase (pgvector) as the canonical store.
- Reuse embedding model `openai/text-embedding-3-small` for canonical writes.
- Preserve current metadata shape and add fields progressively:
  - `content_type`
  - `subject`
  - `topic`
  - `owner`
- Return immediate ack for `/ingest` while full async orchestration is out
  of scope in current scaffold.
- `/ingest` now supports explicit status values:
  - `accepted` (valid request)
  - `queued` (orchestration pending for non-obsidian sources)
  - `failed` (validation rejection with summary details)
- `owner` defaults:
  - request context owner first
  - else `OPENBRAIN_DEFAULT_OWNER`
  - else environment/default `mmcmahon`
- Subject/topic fallbacks:
  - `subject` derives from source filename when not supplied
  - `topic` derives to `YYYY-MM-DD` when not supplied
- `ingest_id` is deterministic (md5 fingerprint) for idempotent retries.

## Resolved (2026-03-21)

- Source reachability: enforced — unreachable inputs return `failed`.
- Owner resolution: token → owner map (`OPENBRAIN_TOKEN_OWNER_MAP`) is the primary path for agent callers. Header `x-openbrain-owner` is the fallback. Body `owner` field is untrusted for scope enforcement.
- Keyword-first surfacing: implemented. Low-confidence threshold for fallback is optional future enhancement.
- `text` source_type: added to allowed set. Word-count guard (413) prevents oversized payloads.
