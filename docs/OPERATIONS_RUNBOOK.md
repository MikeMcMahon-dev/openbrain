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

- Run `scripts/ingest.py` to build/update Chroma index from configured
  sources.
- Expected Chroma behavior:
  - Existing chunks in same IDs are upserted.
  - New docs generate new chunk vectors and metadata.
- If server process is watching source changes, it may trigger reindex
  actions in the same environment.

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
- Query route:
  - Request goes to `/query` (or mode-specific wrapper).
  - Server normalizes `mode`.
  - Embedding generated via existing model.
  - Keyword matches are surfaced first.
  - Vector matches fill remaining slots.
  - Tutor layer receives context and returns prompt payload.

## Linting flow

- `make lint` runs:
  - compile check
  - Ruff checks
  - Markdown lint via `pymarkdown`
- If lint fails, resolve file-level issues before commit.

## Index persistence flow

- `brain_index/` is local generated vector state.
- Re-run ingestion to reproduce.
- If accidentally mutated before commit, restore from HEAD:
  - `git restore -- brain_index`

## Safe commit gates

- Do not include generated artifacts:
  - `brain_index/`
  - bytecode artifacts (`__pycache__`)
- Review `git diff --cached` before every commit.
