# MCP Contract Draft (Current Scope)

This document defines the current OpenBrain MCP API contract for the endpoints planned in Step 4.

## Goals

- Keep transport simple and predictable.
- Preserve vector-first retrieval behavior.
- Defer background orchestration until MCP layer implementation is complete.
- Maintain compatibility with current server field names and metadata model.

---

## Shared request metadata

The following fields are common across ingest and query-like operations:

- `owner` (string, required in payload examples, defaults to `default_user`)
  - Logical tenant/user namespace for future multi-user separation.

- `subject` (string, optional)
- `topic` (string, optional)

These map to future `where` filters and UI context.

---

## `POST /ingest`

### Purpose

Ingest a new study material source into the same Chroma collection used by markdown ingestion.

### Request body

```json
{
  "source_type": "obsidian | pdf | docx | url",
  "source": "path|file|url",
  "subject": "Biology",
  "topic": "Cell respiration",
  "owner": "student_alpha"
}
```

### Response body

```json
{
  "ingest_id": "a1b2c3d4...",
  "status": "queued | accepted",
  "source_type": "pdf",
  "source": "/path/to/file.pdf",
  "owner": "student_alpha",
  "subject": "Biology",
  "topic": "Cell respiration"
}
```

### Contract rules

- Validate `source_type` is one of the supported ingestion sources.
- `status`:
  - `accepted` only when source and type are present/valid.
  - `queued` when values are present but orchestration is not yet complete.
- `ingest_id` is returned immediately for traceability and polling.
- Initial MCP behavior may remain synchronous acknowledgment with async processing planned.

---

## `POST /query`

### Purpose

Retrieve semantically relevant context and return tutor-ready payload.

### Request body

```json
{
  "query": "How does photosynthesis use energy?",
  "mode": "explain | quiz | flashcards",
  "n_results": 5,
  "student_attempt": "optional",
  "owner": "student_alpha"
}
```

### Response body

```json
{
  "mode": "explain",
  "question": "How does photosynthesis use energy?",
  "rules": ["..."],
  "tutor_prompt": "....",
  "context_used": [
    {
      "source": "vault/biology/photosynthesis.md",
      "file": "photosynthesis.md",
      "section": "vault/biology",
      "heading": "# Photosynthesis",
      "text": "..."
    }
  ],
  "results": [
    {
      "score": 1.8,
      "file": "photosynthesis.md",
      "source": "vault/biology/photosynthesis.md",
      "section": "vault/biology",
      "heading": "# Photosynthesis",
      "content_type": "markdown",
      "owner": "student_alpha",
      "text": "..."
    }
  ]
}
```

### Contract rules

- `mode` is normalized to a valid tutor mode (`explain`, `quiz`, `flashcards`).
- Vector retrieval takes priority.
- Keyword fallback is allowed when vector search returns weak/no results.

---

## `POST /generate_quiz`

### Purpose

Shortcut endpoint forcing tutor mode to `quiz`.

### Contract

- Request shape mirrors `POST /query`.
- Server sets `mode: "quiz"` before tutor handling.
- Response uses the same top-level shape as `/query`.

---

## `POST /generate_flashcards`

### Purpose

Shortcut endpoint forcing tutor mode to `flashcards`.

### Contract

- Request shape mirrors `POST /query`.
- Server sets `mode: "flashcards"` before tutor handling.
- Response uses the same top-level shape as `/query`.

---

## Error shape (recommended)

For all endpoints, use a consistent failure shape:

```json
{
  "error": "validation_error",
  "message": "Owner is required.",
  "status": 400
}
```

