# MCP Contract Draft (Current Scope)

This document defines the current OpenBrain MCP API contract for the
endpoints planned in Step 4.

## Goals

- Keep transport simple and predictable.
- Preserve keyword-first retrieval prioritization with vector fallback for
  remaining slots.
- Defer background orchestration until MCP layer implementation is complete.
- Maintain compatibility with current server field names and metadata model.

---

## Shared request metadata

The following fields are common across ingest and query-like operations:

- `owner` (string, required in payload examples, defaults to environment
  or `mmcmahon`)
  - Logical tenant/user namespace for future multi-user separation.

- `subject` (string, optional)
- `topic` (string, optional)

These map to future `where` filters and UI context.

---

## `POST /ingest`

### Ingest purpose

Ingest a new study material source into the same Chroma collection used
by markdown ingestion.

### Ingest request body

```json
{
  "source_type": "obsidian | pdf | docx | url",
  "source": "path|file|url",
  "subject": "Biology",
  "topic": "Cell respiration",
  "owner": "student_alpha"
}
```

### Ingest response body

```json
{
  "ingest_id": "a1b2c3d4...",
  "status": "accepted | queued | failed",
  "source_type": "pdf",
  "source": "/path/to/file.pdf",
  "owner": "student_alpha",
  "subject": "Biology",
  "topic": "Cell respiration",
  "message": "Ingest request accepted; queued in MCP scaffold.",
  "details": ["optional", "failure detail list"]
}
```

### Ingest contract rules

- Validate `source_type` is one of the supported ingestion sources.
- `status`:
  - `accepted`: source and type are valid and trace id is issued.
  - `queued`: accepted request is waiting for MCP orchestration (for
    non-obsidian sources).
  - `failed`: request is rejected due to invalid payload.
- `subject` and `topic` defaults:
  - `subject`: source filename or import label if not provided.
  - `topic`: ingest date `YYYY-MM-DD` if not provided.
- `ingest_id` is returned immediately for traceability and polling.
- `details` contains failure summaries when status is `failed`.
- Initial MCP behavior may remain synchronous acknowledgment with async
  processing planned.

---

## `POST /query`

### Query purpose

Retrieve semantically relevant context and return tutor-ready payload.

### Query request body

```json
{
  "query": "How does photosynthesis use energy?",
  "mode": "explain | quiz | flashcards",
  "n_results": 5,
  "student_attempt": "optional",
  "owner": "student_alpha"
}
```

### Query response body

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
      "source_channel": "keyword",
      "text": "..."
    }
  ]
}
```

### Query contract rules

- `mode` is normalized to a valid tutor mode (`explain`,
  `quiz`, `flashcards`).
- Keyword retrieval is surfaced first when matched; vector retrieval fills
  remaining result slots.
- Keyword fallback is allowed when vector search returns weak/no results.

### Query result metadata

- `source_channel` indicates how each hit was surfaced:
  - `keyword`: match came from BM25/term-based retrieval.
  - `vector`: match came from semantic/vector similarity.

---

## `POST /generate_quiz`

### Quiz purpose

Shortcut endpoint forcing tutor mode to `quiz`.

### Quiz contract

- Request shape mirrors `POST /query`.
- Server sets `mode: "quiz"` before tutor handling.
- Response uses the same top-level shape as `/query`.

---

## `POST /generate_flashcards`

### Flashcards purpose

Shortcut endpoint forcing tutor mode to `flashcards`.

### Flashcards contract

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

## MCP execution step-by-step (current scaffold behavior)

1. Client calls `POST /ingest` with a payload that includes source
   metadata.
2. Server validates `source_type` against allowed values
   (`obsidian`, `pdf`, `docx`, `url`).
3. Server returns an immediate acknowledgement containing:
   - `ingest_id`
   - `status` (`accepted`, `queued`, or `failed`)
   - normalized payload echo fields
   - `message` and optional `details`
4. Client sends query with `owner` and mode.
5. Server normalizes mode and builds query embedding with
   `BAAI/bge-small-en`.
6. Server applies BM25 keyword retrieval and vector retrieval.
7. Keyword-matching hits are surfaced first when present;
   vector hits fill remaining results.
8. Tutor layer maps results into one of:
   - explain
   - quiz
   - flashcards
9. Server returns context + tutor prompt payload to the caller.
