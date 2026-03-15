# API Contract Examples

Concrete request/response payloads for the MCP-style routes implemented
in the server scaffold.

## Shared request shape

```json
{
  "owner": "student_alpha",
  "subject": "Biology",
  "topic": "Cell respiration",
  "query": "How does energy move through photosynthesis?",
  "mode": "explain",
  "n_results": 5,
  "student_attempt": "I think sunlight changes into sugar."
}
```

Notes:

- `owner` is optional in payload and defaults to `mmcmahon` (or
  `OPENBRAIN_DEFAULT_OWNER`) when omitted.
- `subject` and `topic` are currently documented fields and are optional.
- `student_attempt` is used by tutor formatting logic but not required.

## `POST /ingest` request and response

Request:

```json
{
  "source_type": "pdf",
  "source": "~/materials/respiration_notes.pdf",
  "subject": "Biology",
  "topic": "Cell respiration",
  "owner": "student_alpha"
}
```

Accepted response:

```json
{
  "ingest_id": "4b7a6d2e9b1a",
  "status": "accepted",
  "source_type": "obsidian",
  "source": "/Users/me/vault/biology",
  "owner": "student_alpha",
  "subject": "Biology",
  "topic": "Cell respiration",
  "message": "Ingest request accepted."
}
```

Queued response (temporary scaffold state):

```json
{
  "ingest_id": "c0ffee001",
  "status": "queued",
  "source_type": "url",
  "source": "https://example.org/biology/chapter1",
  "owner": "student_alpha",
  "subject": "Biology",
  "topic": "Cell respiration",
  "message": "Ingest request accepted; queued in MCP scaffold."
}
```

Failed response:

```json
{
  "ingest_id": "3c2d1b45e9af",
  "status": "failed",
  "source_type": "pdf",
  "source": "",
  "owner": "student_alpha",
  "subject": "Biology",
  "topic": "2026-03-13",
  "message": "Ingest failed: source is required.",
  "details": ["source field is missing or empty"]
}
```

## `POST /query` response

```json
{
  "mode": "explain",
  "question": "How does energy move through photosynthesis?",
  "rules": [
    "Ask the student to try first.",
    "Use short, simple language for a middle school learner.",
    "Explain ideas step by step.",
    "Encourage effort and curiosity before confirming answers."
  ],
  "tutor_prompt": "Ask me about one step first ...",
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
      "score": 1.24,
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

## Shortcut routes

- `POST /generate_quiz` response mirrors `/query` but enforces
  `"mode":"quiz"`.
- `POST /generate_flashcards` response mirrors `/query` but enforces
  `"mode":"flashcards"`.
