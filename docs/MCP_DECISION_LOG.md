# MCP and Tutor Decision Log

This log captures decisions that currently block deeper implementation and
should be confirmed before code changes.

## Decisions already made

- Keep ChromaDB for now.
- Reuse embedding model `BAAI/bge-small-en`.
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

## Remaining open questions

- Source reachability is required and unreachable inputs now return
  `failed`.
- Owner should be passed explicitly by the caller when available; fallback is
  a temporary safety default.
- Keyword-first surfacing is implemented; explicit low-confidence threshold
  for fallback remains optional future enhancement.
