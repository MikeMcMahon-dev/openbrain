# OpenBrain Operations Runbook

## Daily local development flow

- Set up environment:
  - `python -m venv .venv`
  - `pip install -r requirements.txt`
  - `pip install -r requirements-dev.txt`
- Quick health checks:
  - `pip install -r requirements-dev.txt`
  - `make lint`
- Prepare data sources:
  - Ensure Obsidian vault path in `config/imports.toml`.
  - Ensure `imports.toml` source metadata includes `owner`/`user_id`
    defaults.

## Ingestion flow

- Run `scripts/ingest.py` to write/import chunks into Supabase (`public.thoughts`).
- Expected behavior:
  - Existing chunks with matching deterministic IDs are upserted/skipped via primary keys.
  - New docs generate new chunk records and metadata.

## MCP endpoint runtime flow

- Start server:
  - `uvicorn brain_server.server:app --host 127.0.0.1 --port 8000`
- MCP-style ingest behavior:
  - `/ingest` returns deterministic `ingest_id` for identical payload
    retries.
  - Status transitions:
    - `accepted` (valid obsidian request)
    - `queued` (non-obsidian request accepted but orchestration pending)
    - `failed` (validation errors with `details`)
  - `sources` can be used for bulk ingest requests so local directory
    enumeration scripts can send one request for many files.
- Query route:
  - Request goes to `/query` (or mode-specific wrapper).
  - Server normalizes `mode`.
  - Embedding generated via configured model path.
  - Keyword and vector candidates are merged by the query handler.
  - Tutor layer receives context and returns prompt payload.

## Vercel app post-deploy flow

- Deploys are validated with:
  - `make smoke`
  - `make smoke-live SMOKE_URL=https://openbrain-rouge.vercel.app`
- Smoke expectations include:
  - `/health` and `/api/health` return HTTP 200
  - `/query`, `/api/query`, `/search`, `/api/search` return HTTP 200
  - `/generate_quiz`, `/api/generate_quiz`, `/generate_flashcards`, `/api/generate_flashcards` return HTTP 200
  - `/api/ingest` returns a valid payload with expected keys and status.
- `GET /` loads the Vercel demo page and static assets.
- `/query`, `/search`, `/generate_quiz`, `/generate_flashcards`, `/ingest` are routed through API handler for compatibility.
- Keep at least one successful log + smoke run in the deployment record before demo or demo-family handoff.

## Linting flow

- `make lint` runs:
  - compile check
  - Ruff checks
  - Markdown lint via `pymarkdown`
- If lint fails, resolve file-level issues before commit.

## Index persistence flow

- There is no runtime dependency on local Chroma persistence for production.
- Re-run ingestion to reproduce from source material.
- If accidental generated artifacts are created locally, clean them before commit.

## Safe commit gates

- Do not include generated artifacts:
  - bytecode artifacts (`__pycache__`)
  - temporary local vector cache/state generated during experiments
- Review `git diff --cached` before every commit.
