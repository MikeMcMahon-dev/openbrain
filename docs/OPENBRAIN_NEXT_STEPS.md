# OpenBrain Next Steps

This document tracks planned improvements for the OpenBrain system.

---

## OpenBrain 2.0 — Status (2026-05-13)

Design complete. Stage 1 (ADR + Domain Discovery) done. Awaiting human sign-off before Stage 2.

### Stage 1 — ADR + Domain Discovery ✅ Complete
- **Branch:** `docs/ob2-architecture-decisions`
- ADR-007: Unified knowledge table
- ADR-008: Temporal lifecycle + `component:*` canonical tag deduplication
- ADR-009: Wiki layer — explicit compilation only
- ADR-010: Write safety — INSERT-only agents, two-step supersession
- Domain discovery: `docs/migrations/001_domain_discovery.md`

**HUMAN ACTION REQUIRED before Stage 2:**
1. Review the YAML mapping block in `docs/migrations/001_domain_discovery.md`
2. Verify the `engineering/notes` (391 rows) and `project/documentation` (113 rows) mappings
3. Confirm Supabase dashboard backups are enabled (Settings → Backups)
4. Sign off → Stage 2 can proceed

### Stage 2 — Database Migration 🔜 Pending human sign-off
- Branch: `feat/ob2-schema-migration`
- Schema SQL: `supabase/migrations/001_knowledge_table.sql`, `002_wiki_pages.sql`
- Migration script: `scripts/migrate_thoughts.py` (dry-run first, `--execute` after approval)
- Do NOT run `--execute` without explicit human confirmation

### Stage 3 — API Updates 🔜 Pending Stage 2
- Branch: `feat/ob2-api-endpoints`
- New endpoints: `/api/ingest_state`, `/api/propose_supersession`, `/api/confirm_supersession`,
  `/api/query_state`, `/api/compile_wiki`, `/api/wiki/{page_name}`

### Stage 4 — Portfolio Documentation 🔜 Pending Stage 3
- Branch: `docs/ob2-portfolio-documentation`
- Blog post: `project-openbrain-2-architecture.mdx` on mikemcmahon.dev
- OB2 user guide for Claude session startup protocol

### Key schema finding (Stage 1)
`public.thoughts` does NOT have direct `subject`/`topic` columns. They live in `metadata` JSONB.
Migration script must use `metadata->>'subject'` and `metadata->>'topic'`, not direct columns.
The OB2 design spec SQL queries need this adaptation.

---

## Last Successful Checks (2026-05-01)

- Deployment smoke: `https://openbrain-rouge.vercel.app`
  - Live smoke: **30/30** (includes 4 new OAuth flow cases)
  - Vault queries returning real content — `kubernetes` high, `Talos` high, `openbrain` medium
- DB state: 668+ rows, writes confirmed under `mike.mcmahon67`
- Claude.ai native MCP connector: **live** — OAuth completes, all 4 tools available
- SSH commit signing configured for `claude` OS user, verified on GitHub
- Git:
  - Latest merged commit: `67dd916` (main)
  - PRs shipped: #34 guardrails, #35 MCP routing, #36 notifications, #37 OAuth,
    #38 query-params, #39 URL-decode, #40 lint+dev-install

---

## Session Notes — 2026-05-01

### What shipped
- **Token rotation** — `opbr_*` bearer tokens rotated, `docs/MCP_SETUP.md` scrubbed of real token
- **Pre-commit guardrails** (`scripts/pre-commit`) — blocks direct commits to main and `opbr_` token patterns; `make install-hooks` / `make dev-install` wire it
- **MCP HTTP endpoint** — `/mcp/messages` route added to `vercel.json`; `notifications/initialized` handling added to `mcp_http.py`
- **OAuth 2.0 server** (`api/oauth.py`) — stateless HMAC-signed authorization codes, PKCE S256, 5-minute code TTL; endpoints: `/.well-known/oauth-authorization-server`, `/authorize`, `/token`
- **Two routing bugs fixed** — `queryStringParameters` vs `query` key mismatch in `handle_authorize`; `urllib.parse.unquote_plus` missing from `index.py` query string parser (caused `redirect_uri` to arrive URL-encoded → garbled Location header)
- **OAuth smoke tests** — 4 new cases in `smoke_checks.py` covering discovery, authorize redirect, token exchange, and unknown-client rejection
- **SSH commit signing** for `claude` OS user — ed25519 key at `~/.ssh/id_ed25519_signing`, global git config set, signing key on CC-mcmahon-dev GitHub account
- **SUPABASE_DB_URL trailing quote** — found in Vercel Shared Variables; trailing `"` made database name `postgres"` → all DB reads failed silently for ~2-3 weeks

### Key lessons learned
- **Vercel Shared Variables** don't appear in project-level env var listings (API or dashboard project view); look in the Shared Variables section separately
- **`retrieve_thoughts` swallows all DB exceptions** — a broken DB URL returns HTTP 200 with `results: []`, indistinguishable from "no matching content"; the smoke suite cannot detect this
- **`index.py` is a local approximation** of the Vercel runtime; use `vercel dev` to catch runtime-specific bugs (query param encoding, `queryStringParameters` key name) before pushing
- **Trailing quote in env var** — pasting a shell-quoted value (`"postgresql://...6543/postgres"`) into Vercel stores the quotes as literal characters; always paste the raw URL

### Pending backlog items added
- `/health` endpoint DB connectivity check — `SELECT 1` with non-200 on failure
- Canary ingest→query smoke test — write known phrase, verify retrieval, catch silent DB failures

---

## Step 1 – Supabase as Primary Storage (Complete)

Primary storage now targets Supabase with pgvector:

- `thoughts` is the canonical source for Slack capture
- `openai/text-embedding-3-small` is the single embedding model for all canonical writes
- ChromaDB references are retired from runtime; Supabase is canonical.

---

## Step 2 – Retrieval and Tutor Baseline (Complete)

Keep existing retrieval capabilities while routing to Supabase source:

- retrieval API contracts are now exercised through Vercel serverless handlers in `api/`
- tutor endpoints (`/query`, `/generate_quiz`, `/generate_flashcards`) are live on both legacy and `/api` routes
- keyword + vector hybrid ranking remains active in current query path.

---

## Step 3 – Tenant & Ownership Groundwork (Complete)

Stand up schema-level tenancy before Vercel rollout:

- tenant and durable identity fields have been added for row ownership and isolation.
- shared/private/public intent fields and deterministic source IDs are in place for policy + re-import idempotence.
- tenancy tables and RLS scaffolding are in place and migration-applied.

Files:

- `20260315193000_supabase_primary_schema_and_tenancy.sql`
- `20260315195000_add_user_identity_tenancy_fields.sql`
- `20260315200000_enable_thoughts_rls.sql`

Planned follow-up:

- validate and enforce tenant-aware auth context mapping (`supabase_user_id`, `email`, or `slack_user_id`)
- add tenancy-aware query filters in API handlers (deployed)

---

## Step 4 – Slack Ingestion (Complete)

Slack message ingestion now works end-to-end in Supabase:

- `supabase/functions/ingest-thought/index.ts` receives events
- signature verification is enforced for Slack callbacks
- startup guards block slash commands and non-user message events
- `slack_username` is captured and persisted
- `slack_user_id` and tenant metadata are now written on ingest
- inserts to `thoughts` table are confirmed
- function returns confirmation back in the Slack thread

Current operational state:

- Project: `edljijurbmcupawnjpfx`
- Function endpoint:
  - `https://edljijurbmcupawnjpfx.supabase.co/functions/v1/ingest-thought`
- Migration artifacts added:
  - `20260314193123_add_slack_username.sql`
  - `20260315193000_supabase_primary_schema_and_tenancy.sql`
  - `20260315195000_add_user_identity_tenancy_fields.sql`
  - `20260315200000_enable_thoughts_rls.sql`

---

## Step 5 – MCP Layer (Complete)

All target endpoints live and auth-gated on Vercel:

- `POST /openbrain_query` + `/tools/openbrain_query`
- `POST /openbrain_generate_quiz` + `/tools/openbrain_generate_quiz`
- `POST /openbrain_generate_flashcards` + `/tools/openbrain_generate_flashcards`
- `POST /openbrain_ingest` + `/tools/openbrain_ingest`
- `POST /claude_query`, `/claude_generate_quiz`, `/claude_generate_flashcards`, `/claude_ingest` (Claude tool_use envelope)

Claude Code MCP server: `mcp_server/openbrain.py` — registered via `.mcp.json`, exposes all four tools natively in Claude Code sessions.

Detailed contracts: `docs/MCP_CONTRACT.md`, `docs/API_CONTRACT_EXAMPLES.md`

---

## Step 6 – Vercel App Implementation (Complete, hardening ongoing)

- web interface for thought capture, query, and generation is deployed at `https://openbrain-rouge.vercel.app/`
- app routes now support both legacy and `/api` endpoints
- tenancy context is resolved from request headers (`x-openbrain-owner`, `x-openbrain-tenant-id`) for user separation
- next work: secure auth binding and production-grade tenancy enforcement

---

## Step 7 – Vercel Rollout + Obsidian Backfill (Execution)

Before defaulting reads to Supabase:

- Re-import Obsidian markdown corpus into Supabase with deterministic `source_chunk_id`
- Verify row/document counts and coverage against baseline
- Run rollout smoke checks in `docs/VERCEL_SMOKE_CHECKS.md`
- Proceed to manual filesystem import test as your next operational validation
- Gate default source flip on parity results and rollback readiness

## Step 8 – Agent Communication Layer (Complete)

### What is done
- `api/_openbrain_api.py` — agent-agnostic core: query, search, ingest, hybrid retrieval, preflight, tutor packet generation.
- `api/chatgpt.py` — thin adapter for ChatGPT tool_use envelope (`tool_input` / `input` / `arguments`). Handles per-user token → owner resolution via `OPENBRAIN_TOKEN_OWNER_MAP`.
- `api/claude.py` — thin adapter for Claude native `tool_use` format (`input` key).
- All routes live on Vercel, auth-gated, smoke-tested.
- `OPENBRAIN_TOKEN_OWNER_MAP` — JSON env var mapping per-user bearer tokens to owner strings. Three family members each have an isolated token and data scope.
- Custom GPT OpenAPI spec: `docs/CUSTOM_GPT_ACTION_SPEC.yaml` (OpenAPI 3.1.0)
- Three family Custom GPTs configured: Mike, Beth (snapple01), Annie (anneliesepaige)
- Text ingest (`source_type=text`) live and working. Word-count guard: 413 returned if payload exceeds `OPENBRAIN_TEXT_INGEST_MAX_WORDS` (default 6000).
- MCP server for Claude Code: `mcp_server/openbrain.py`

### Design constraints preserved
- `api/chatgpt.py` and `api/claude.py` stay thin platform adapters — all logic in `_openbrain_api.py`.
- Owner/tenant resolved from request headers, not request body.
- `ingest_id` is deterministic (md5) for idempotent retries.

## Session Handoff (2026-03-21)

What was done this session:

- Transaction pooler (port 6543) confirmed working end-to-end.
- Identity bridge deployed: per-user token → owner mapping for Mike, Beth, Annie.
- Tokens rotated after discovering prior tokens in git history.
- Custom GPT OpenAPI spec written and validated. Three family Custom GPTs created.
- Text ingest source_type fixed (was silently failing). Word-count guard added.
- Claude MCP adapter (`api/claude.py`) written and deployed.
- MCP server built and registered in Claude Code via `.mcp.json`.
- DB cleanup: 380 `default_user` duplicate rows deleted, 10 orphaned rows re-attributed.
- Git best practices doc written and ingested into brain.
- `gh` CLI installed.

Immediate next actions:
1. Annie's school content import (coordinate when she brings laptop post-Spring Break).
2. Ingest Custom GPT share URLs into brain once all three confirmed.
3. DB health automation — planned as K8s CronJob (separate project).

---

## Step 9 – Query Tuning + Confidence Scoring (Complete 2026-03-27)

Replaced naive keyword-first merge with Reciprocal Rank Fusion (RRF) + length penalty:

- **RRF fusion** (`k=60`): documents appearing in both keyword and vector lists get boosted; no channel unconditionally wins.
- **Length penalty**: docs with <30 words are down-weighted proportionally — eliminates short-message noise (Slack, instruction snippets) dominating results.
- **Confidence score**: every result carries `confidence: high | medium | low`. Top-level `query_confidence` added to query API response so GPTs and callers know when to caveat answers.
- **Test harness**: `scripts/test_query_harness.py` — 1000-query suite, vault + adversarial + naive queries. Pass criteria: ≥90% relevancy in position 1 or 2.
- **Results log**: `scripts/query_test_results.md` — timestamped per run, tracks improvement across iterations.
- Baseline result: **97% at 100 queries, 96.9% at 1000 queries**. Certified trustworthy.

## Step 10 – Production Hardening (Complete 2026-03-27)

- **Vercel runtime fix** (`api/index.py`): Vercel silently updated `@vercel/python` to detect ASGI/WSGI apps by name. Renamed `from api import app` → `from api.app import handler as _route` to eliminate the name collision. All Python functions were returning 500 until this was deployed.
- **Application-level error logging**: `log_error()` in `_openbrain_api.py` writes structured error records (path, method, exc_type, traceback, Vercel request_id) to `public.error_log` with autocommit. `api/index.py` wraps every request in try/except that calls `log_error` before re-raising. `scripts/error_log.py` provides CLI access (`--hours`, `--tail`, `--full`).
- **GPT confidence instructions**: All three Custom GPT system prompts updated to act on `query_confidence` field — high/medium/low each produce distinct user-facing behavior. Annie's prompt additionally enforces consistent tutor voice on web-sourced answers.
- **Supabase anon key lockdown**: `REVOKE ALL ON public.error_log FROM anon/authenticated` applied to match `thoughts` table lockdown.

---

## Step 11 – Dual-Judge Answer Fidelity Eval Harness (Complete 2026-03-26)

Built `scripts/test_answer_fidelity.py` — a 25-case eval harness that measures answer quality end-to-end, distinct from retrieval quality:

- **Generator**: `claude-haiku-4-5-20251001` simulates the GPT layer — generates answers from retrieved chunks only.
- **Judge A**: `claude-sonnet-4-6` (Anthropic) scores fidelity 0.0–1.0, hallucination true/false, confidence, reasoning.
- **Judge B**: `gpt-4o` (OpenAI, key loaded from agent-lab) provides independent second opinion.
- **Agreement logic**: both judges within 0.15 fidelity + matching hallucination flag = high-confidence result. Divergence beyond that threshold triggers human review flag.
- **Escalation rule**: if non-adversarial disagreement rate exceeds 30%, harness warns and documents before proceeding.
- **25 test cases**: 10 Mike infra (validated against vault + HashiCorp/RedHat docs), 10 Annie study (vault only), 5 adversarial hallucination traps (designed to detect made-up facts for questions not in brain).
- **Graceful degradation**: if OpenAI key unavailable, runs in single-judge mode with results flagged accordingly.
- **Shared eval history**: both harnesses append to `scripts/eval_history.md` for cross-run tracking.
- **Baseline results**: see `scripts/answer_fidelity_results.md`.
- **Methodology**: see `docs/EVAL_METHODOLOGY.md`.

Files added/modified:
- `scripts/test_answer_fidelity.py` — main harness
- `scripts/answer_fidelity_results.md` — per-run results
- `scripts/eval_history.md` — shared history log
- `scripts/test_query_harness.py` — updated to append to eval_history.md
- `docs/EVAL_METHODOLOGY.md` — dual-judge methodology documentation
- `.env.local` — annotation confirming OPENAI_API_KEY is consumed from agent-lab, not stored here

---

## Step 12 — PDF Ingestion (Complete 2026-03-31)

`source_type=pdf` now extracts and writes to Supabase. Previously returned `status: "queued"` and silently dropped content.

- `pypdf==6.9.2` added to `requirements.txt`
- `_extract_pdf(source)` added to `api/_openbrain_api.py` — local import, raises `ValueError` on failure
- Large PDFs (>6000 words) chunked into 1500-word segments and written separately
- Empty/image-only PDFs return `status: "failed"` with clear message
- `make pdf-unit` — 6/6 passing
- `make smoke` — 29/29 passing (includes 3 new PDF cases)

**Known limitation — ChatGPT action interface:**
ChatGPT Custom GPT actions cannot submit raw binary files. The `source_type=pdf` path is for **server-side batch ingest scripts only**. GPTs submit as `source_type=text` with verbatim extracted content.

**Required follow-up:**
- Deploy to Vercel and run `make smoke-live` / `make pdf-eval-live` to confirm ≥80% pass rate
- Apply updated system prompt and action spec to Beth's GPT config (repo is up to date)

---

## Step 13 — DOCX and URL Ingestion (Complete 2026-03-31)

`source_type=docx` and `source_type=url` now extract and write to Supabase.

- `python-docx==1.2.0` added to `requirements.txt`
- `_extract_docx(source)` added to `api/_openbrain_api.py` — local import, raises `ValueError` on failure
- `_fetch_url(source)` added to `api/_openbrain_api.py` — stdlib urllib + html.parser, User-Agent header, 15s timeout
- Both wired into `ingest_payload()` elif branches with chunking (1500-word chunks for docs >6000 words)
- `make docx-unit` — 6/6 passing
- `make url-unit` — 5/5 passing
- `make smoke` — 35/35 passing (includes 6 new DOCX + URL cases)

**URL ingestion limitations:**
URL ingestion uses stdlib urllib + html.parser (no requests/BeautifulSoup). Content quality depends
on site structure — navigation/boilerplate text will be included. Not suitable for JavaScript-rendered
pages (no headless browser). Sufficient for documentation and article URLs.

**Required follow-up:**
- Deploy to Vercel and run `make smoke-live` / `make docx-url-eval-live` to confirm ≥75% pass rate
- Apply updated GPT system prompts and action spec to all three GPT configs (repo is up to date)

---

## Step 14 — Structured Markdown Ingest Pipeline + Vision OCR (Complete 2026-04-02)

All four ingestors now produce structured markdown. Sliding-window chunking replaced by heading-split chunking with token ceiling. Vision OCR path added for scanned PDFs. (ADR-006)

### What shipped (PRs #23–27, merged to main)

- **PDF three-way classification** (`scripts/ingestors/pdf.py`): avg chars/page determines path
  - `text_dominant` (≥200 chars/page): pymupdf4llm → structured markdown
  - `mixed` (50–199 chars/page): pymupdf4llm → structured markdown
  - `image_dominant` (<50 chars/page): pymupdf page render → Pillow contrast(2.0x)/sharpen(1.5x) → Claude Haiku vision OCR → markdown
- **DOCX heading preservation** (`scripts/ingestors/docx.py`): `para.style.name` → `#/##/###/####` headers; `content_type="markdown"`
- **URL markdownify + boilerplate stripping** (`scripts/ingestors/url.py`): BeautifulSoup content extraction (main/article/role=main) + markdownify with nav/footer/header/aside strip; replaces raw `get_text()`
- **chunk_markdown() token ceiling** (`scripts/chunking/markdown.py`): 600-token sub-chunking — oversized sections split with parent heading inherited; eliminates 20% sliding-window overlap redundancy
- **Beth validation UX** (`docs/gpt_instructions/anneliesepaige.md`): post-ingest spot-check shows 3 sample items (start/middle/end) from GPT context window; no hidden folders, no DB query needed
- **A/B test harness** (`scripts/test_pdf_pipeline_ab.py`): baseline confirmed 20% overlap redundancy eliminated, 1→13 chunks on text-dominant fixture
- **Vercel bundle compliance**: pymupdf4llm/Pillow in `requirements-full.txt` only (local); Vercel API retains pypdf — stays under 250MB bundle constraint

### Environment variables added
- `OPENBRAIN_OCR_MODEL` — defaults to `claude-haiku-4-5-20251001`
- `OPENBRAIN_OCR_CONTRAST` — defaults to `2.0`
- `OPENBRAIN_OCR_SHARPEN` — defaults to `1.5`

### OCR eval (multi-agent-lab PR #4, merged)
OCR quality eval added to multi-agent harness (`agents/ocr_eval.py`). Two scenarios in `taskfiles/eval-scenarios.yaml`:
- `ocr-handwritten-biology` — 1-page handwritten notes, ground-truth phrase scoring, pass_threshold 0.80
- `ocr-geometry-degraded` — 40-page poor-quality scan, content-presence scoring, pass_threshold 0.50

**Required follow-up (Part 2):**
- Run OCR eval across all 5 models (Haiku/Sonnet/Opus/GPT-4o/GPT-4o-mini) — results pending, in progress
- chunk_markdown() evaluation against real Annie study PDFs with headings (needs OCR output to produce heading structure)
- markdownify boilerplate tuning — test against real URLs with heavy nav/footer
- Source channel tracking on ingest (ChatGPT action vs direct API — forensic value)

---

## Future Considerations

### Near-Term

- **`/health` DB connectivity check** — add `SELECT 1` to the health endpoint; return non-200 if DB is unreachable. Currently a broken `SUPABASE_DB_URL` is invisible — queries return HTTP 200 with empty results. A real health check makes this detectable by Vercel uptime monitoring and the smoke suite.

- **Canary ingest→query smoke test** — ingest a known test phrase, immediately query for it, assert at least one result returns. Closes the gap where `retrieve_thoughts` swallows DB exceptions and the smoke suite can't distinguish "no content" from "DB broken."

- **Weekly query eval automation** — schedule `scripts/test_query_harness.py` to run every Thursday (100-query pass first, then 1000). Results appended to `scripts/query_test_results.md`. Alert if pass rate drops below 90%. Currently manual; target: Claude Code scheduled agent, then K8s CronJob.

- **Study Buddy session auto-logging** — modify Annie's Custom GPT system prompt to automatically commit a structured session summary (topics covered, correct/incorrect/close items) to the brain at the end of every study session. Eliminates reliance on manual "commit to brain" prompts. Particularly important for test prep — captures performance data before memory fades.

- **NVIDIA ticket automation** — near-term project, high personal impact. Build automated pipeline for NVIDIA support ticket ingestion and tracking.

- **Annie's school content import** — ongoing, more material to ingest as school year continues. Coordinate when new content is available.

- **Ingest Custom GPT share URLs** — once all three GPTs are confirmed stable long-term, ingest their share URLs into the brain for reference.

### Medium-Term

- **RLS Phase 2 enforcement** — proper row-level security in Supabase. Immediate mitigation (anon/authenticated role revocation) is in place. Full RLS is a separate project.

- **DB health automation** — planned as K8s CronJob. Includes: duplicate detection, orphaned row cleanup, embedding coverage check. Separate project from OpenBrain core.

- **Slack `/brain ask` interface** — surface brain queries directly from Slack without switching to ChatGPT. Was an original use case; revisit once core system is stable.

### Long-Term / K8s Deployment Targets

When a Claude agent is running on a K8s/Linux VM node, the following scripts are candidates for automated scheduling:

- `scripts/rotate_tokens.py` — semi-annual bearer token rotation (Jan 1 / Jul 4). Only manual step remaining: update ChatGPT Custom GPT configs (no API available).
- `scripts/test_query_harness.py` — weekly retrieval quality eval.
- DB health checks — duplicate/orphan detection, embedding coverage.

---

## Open Investigation: Duplicate Nightly Reports (2026-04-03)

User receiving two identical session reports 7 minutes apart nightly. Confirmed: single vercel.json cron entry, single REPORT_CONFIGS, _send_email sends one call with all recipients in `to` array simultaneously. 7-minute gap is consistent with Vercel retrying a slow/timed-out cron invocation (Hobby tier behavior). Cannot confirm — Hobby logs only retained 1 hour.

**To investigate next time report fires:** check Vercel logs immediately after 9pm MDT. Look for two cron invocations vs. one invocation + one retry. If retry: add response time logging to cron_session_report.py, or optimize the report build to respond faster (Resend call is the likely slow path).

---

## Long-Term Direction

OpenBrain becomes the family-facing, multi-user knowledge memory with:

- material ingestion (notes, PDFs, DOCX, URLs)
- retrieval and tutor support
- strong tenancy and privacy boundaries
- production-friendly maintenance and operations
