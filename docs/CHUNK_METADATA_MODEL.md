# Chunk Metadata Model (Ingestion + Retrieval)

This model extends the existing Chroma metadata schema while preserving current fields.

## Existing required metadata (current)

These fields are already used and must remain:

- `source`: source path/uri for the raw material.
- `file`: source filename.
- `section`: directory-like location or document section.
- `heading`: nearest markdown-like heading context (or `root` fallback).
- `chunk`: integer chunk index within one source document.
- `owner`: ownership namespace for query filtering.

## Newly required for new material types

- `content_type`: one of `markdown`, `pdf`, `docx`, `url`.

## Optional future-facilitating metadata

- `subject`: educational subject label for curriculum mapping.
- `topic`: fine-grained topic label within a subject.

## Recommended full metadata object

```json
{
  "source": "/abs/path/or/url",
  "file": "cell_biology.pdf",
  "section": "vault/science/cells",
  "heading": "root",
  "chunk": 3,
  "content_type": "pdf",
  "owner": "student_alpha",
  "subject": "Biology",
  "topic": "Cellular respiration"
}
```

## Rules

- Keep `source`, `owner`, `content_type`, and `chunk` on every chunk.
- Continue to keep historical behavior for markdown: `heading` should still describe the current heading context.
- For `pdf`, `docx`, and `url` ingestors:
  - `heading` may be generated section labels where available.
  - `section` should remain stable per source segment.
  - `file` may be a sanitized filename or last URL segment.
- Indexing should not fail when `subject`/`topic` are absent.

## Backfill strategy for migration

- Existing chunks without `content_type`:
  - set to `markdown` for current vault data.
- Existing chunks without `subject`, `topic`:
  - set to `null` or leave omitted (query layer treats as optional).
- Existing chunks without `owner`:
  - set a default user (`default_user`) during backfill to preserve current behavior.

## Retrieval implications

- Filter by `owner` first to isolate user context.
- Filter by `content_type` when UI or tutor mode requests a study material origin.
- Keep `section` and `heading` for context expansion logic and result provenance.

