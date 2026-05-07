# OpenBrain — Working Instructions

## What this project is
Personal/family RAG knowledge system. Supabase (pgvector) is canonical storage. Vercel is the
production API layer. Three family Custom GPTs (mike.mcmahon67, snapple01, anneliesepaige).
Claude Code MCP server for direct vault queries. Production URL: openbrain-rouge.vercel.app

## Current state (as of 2026-03-30)
- Bearer token auth on all endpoints (OPENBRAIN_TOOL_ACCESS_TOKEN / OPENBRAIN_TOKEN_OWNER_MAP)
- Cross-tenant guard: require_auth_owner() binds token to owner, 403 on mismatch
- Query audit log: public.query_log — every query_payload call writes a row
- SafeIngest two-layer gate: regex ($0.00) → optional Haiku classifier on match
- Session report: POST /session_report (manual) + GET /api/cron/session_report (nightly 03:00 UTC)
- Cron reads from BOTH public.thoughts (study notes) AND public.query_log (queries)
- Smoke test: 26/26 green on production — run before any merge to main

## Key architectural decisions
See docs/decisions/ for full ADRs. Summary:
- **Supabase**: canonical storage — pgvector + psycopg direct connection (not REST client) for writes
- **Hybrid retrieval**: RRF + length penalty fusion — 96.9% pass rate baseline (1000-query)
- **Owner resolution**: headers first (x-openbrain-owner), then env default, then mmcmahon
- **Ingest idempotency**: deterministic ingest_id — re-ingesting same content is safe
- **SOCrATIC_RULES**: hardcoded in Python in tutor.py — injected content cannot override behavior
- **Vercel Cron over pg_cron**: pg_cron not enabled by default in Supabase; scheduling belongs in app
- **Report source**: public.thoughts for study notes + public.query_log for queries (both required)

## Git guardrails — NON-NEGOTIABLE

`scripts/pre-commit` is the enforcement layer. Run `make install-hooks` once after cloning.

- **`--no-verify` is banned.** Never use it. If the hook blocks a commit, fix the root cause.
- **`gh` CLI is read + create only**: view PR status, create PRs. No `gh pr merge`, no operations that bypass the review step.
- **Never write real credentials into any file.** Docs and setup guides use placeholders (`YOUR_TOKEN_HERE`). Real tokens live in `.env.local` (gitignored) and Vercel env vars only.
- All changes go feature branch → PR → merge. The hook enforces the branch side; the rest is on you.

## Security constraints — MANDATORY

### Credential incidents
| Date | File | What happened |
|---|---|---|
| 2026-05-01 | `docs/MCP_SETUP.md` | Real API token committed (Claude Code) — remediated same day |
| 2026-05-06 | `ai-engineering-plan/CURRENT_STATE.md` | Proxmox token in multi-agent context |

### Rules
- **Before writing any doc or config**, `credential_scan.py` runs via PreToolUse hook. Do not bypass.
- **Never write real credentials** — use `YOUR_TOKEN_HERE`, `<redacted>`, or `$ENV_VAR`.
- **Haiku must not be used** for documentation or configuration file generation tasks.
- Run `pre-commit run --all-files` before pushing (TruffleHog is configured in home-lab root).

## Critical constraints
- SUPABASE_DB_URL in Vercel must be the **direct Postgres URI** (postgresql://), not the REST URL
- psycopg uses port 6543 (transaction pooler) for serverless — not 5432
- urllib requests to Resend require explicit User-Agent header (Cloudflare blocks Python default)
- Cron date uses `yesterday` UTC — fires at 03:00 UTC (9pm MDT), data is from prior UTC day
- REPORT_CONFIGS owner must match created_by_user_login in public.thoughts exactly
- Never commit directly to main — use /commit skill and PR workflow

## Dev workflow
```bash
# Local dev
cd open-brain
source .venv/bin/activate
python -m api.app  # local server

# Lint
ruff check api/

# Local smoke
python scripts/smoke_checks.py

# Live smoke (preview)
python scripts/smoke_checks.py --live https://<preview-url>.vercel.app

# Live smoke (production)
python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app
```

## File map
| Path | Purpose |
|---|---|
| `api/_openbrain_api.py` | Core: query, ingest, auth, log_query — agent-agnostic contract |
| `api/app.py` | Router — all paths including /openbrain_* and /claude_* |
| `api/chatgpt.py` | Thin adapter for ChatGPT tool_use envelope |
| `api/session_report.py` | Manual session report handler + shared fetch/build/send functions |
| `api/cron_session_report.py` | Vercel Cron handler — reads REPORT_CONFIGS, calls session_report functions |
| `api/tutor.py` | SOCrATIC_RULES enforcement — hardcoded, not DB-driven |
| `scripts/smoke_checks.py` | 26-case smoke test suite |
| `docs/decisions/` | Architecture Decision Records |
| `docs/OPENBRAIN_NEXT_STEPS.md` | Canonical backlog — update at end of every session |
| `vercel.json` | Routes + cron schedule (0 3 * * * = 9pm MDT) |
