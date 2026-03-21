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
- Local CLI ingestion requires a local `.env.local` or `.env` file at the repository root with:
  - `SUPABASE_DB_URL` (Transaction pooler URL, port 6543)
- Local ingestion is for local files only; Vercel env vars are not used by this CLI path.
- On IPv4-only networks, use a Supabase pooler connection URI from
  `Settings -> Database -> Connection string` (Session/Transaction Pooler),
  not the direct `db.<ref>.supabase.co` endpoint.
- Expected behavior:
  - Existing chunks with matching deterministic IDs are upserted/skipped via primary keys.
  - New docs generate new chunk records and metadata.
- Ingestion runbook includes pre-flight behavior:
  - Confirm owner, tenant, and target source are what you expect before approving intake.
  - Expected warning: `no OPENROUTER_API_KEY` means local embedding fallback may be used.
  - Expected error: missing required DB schema fields, DB URL, or unresolved DB host must be fixed before continuing.
  - Pre-flight reports existing row count for the same source/owner/tenant.
  - If pre-flight returns errors, treat ingest as blocked and do not retry until fixed.

## MCP endpoint runtime flow

- Vercel handles all MCP-style routes — no local server required.
- For Claude Code native access, the MCP server runs automatically via `.mcp.json`:
  - `mcp_server/openbrain.py` is spawned by Claude Code as a stdio process.
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
  - `.venv/bin/python scripts/smoke_checks.py`
  - `.venv/bin/python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app`
- Smoke expectations include:
  - `/health` and `/api/health` return HTTP 200
  - `/query`, `/api/query`, `/search`, `/api/search` return HTTP 200
  - `/generate_quiz`, `/api/generate_quiz`, `/generate_flashcards`, `/api/generate_flashcards` return HTTP 200
  - `/api/ingest` returns a valid payload with expected keys and status.
- Add pre-flight verification to smoke checks if needed:
  - `.venv/bin/python scripts/smoke_checks.py --idempotency-source /tmp/openbrain-single/focus.md --idempotency-owner <owner>`
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
