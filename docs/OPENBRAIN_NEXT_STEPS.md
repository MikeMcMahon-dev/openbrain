# OpenBrain Next Steps

This document tracks planned improvements for the OpenBrain system.

---

## Step 1 – Retrieval and Storage Baseline (kept)

Keep the current vector pipeline:

- ChromaDB persistent collection `openbrain`
- Embedding model `BAAI/bge-small-en`
- Markdown ingestion + heading chunking
- Existing metadata keys: `source`, `file`, `section`, `heading`, `chunk`

---

## Step 2 – Tutor-First Query Modes

Add learner-focused tutor behavior in addition to retrieval:

- explain
- quiz
- flashcards

Socratic rules:

- Ask students to attempt answers first.
- Use simple middle-school language.
- Explain step-by-step.
- Encourage effort.

Implementation:

- `scripts/tutor.py` contains tutor policy and prompt payload generation.
- `scripts/query.py` supports all modes and surfaces
  keyword matches before vector-only hits.

---

## Step 3 – Multi-format Ingestion Extension

Add additional ingestion inputs while preserving
current embedding model and collection:

- PDF
- DOCX
- Website URL

Implementation locations:

- `scripts/ingestors/markdown.py`
- `scripts/ingestors/pdf.py`
- `scripts/ingestors/docx.py`
- `scripts/ingestors/url.py`
- `scripts/chunking/markdown.py`
- `scripts/chunking/text.py`
- `scripts/ingest.py` (coordinates all content types)

Metadata:

- Preserve existing keys.
- Add `content_type` everywhere.
- Add `subject` and `topic` where known.

---

## Step 4 – MCP Layer Design

Target endpoints to expose:

- `POST /ingest` (interface scaffold)
- `POST /query`
- `POST /generate_quiz`
- `POST /generate_flashcards`

Detailed contracts are now tracked in:

- `docs/MCP_CONTRACT.md`
- `docs/API_CONTRACT_EXAMPLES.md`
- `docs/CHUNK_METADATA_MODEL.md`
- `docs/OWNER_TENANCY_NOTES.md`
- `docs/TUTOR_BEHAVIOR_CONTRACT.md`
- `docs/OPERATIONS_RUNBOOK.md`
- `docs/SUPABASE_MIGRATION_PATH.md`

Current status:

- API routes are scaffolded in `brain_server/server.py`.
- Full ingestion orchestration through MCP transport is planned next.
- Decision log tracked in `docs/MCP_DECISION_LOG.md`
  for unresolved implementation questions.

---

## Future Considerations

- Keep keyword-first retrieval surfacing, then vector fallback until
  hybrid ranking is built.
- Add richer hybrid scoring in a later pass.
- Move metadata routing into shared retrieval abstractions.
- Evaluate Supabase as a managed Vector DB candidate if scale requires:
  - built-in auth
  - managed hosting
  - easier multi-device sharing
  - migration overhead vs current local-first simplicity

---

## Long-Term Direction

OpenBrain becomes the central student learning memory with:

- material ingestion (notes, PDFs, DOCX, URLs)
- retrieval and Socratic guidance
- future quiz/flashcard generation quality improvements
