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

## Schema & migration changes — MANDATORY (ADR-018a §10d)
Before authoring OR applying any `ALTER`/`DROP` migration, run the preflight and validate from
its evidence — never from memory. "Validate current state" is NOT done after checking row data
and wording; it must cover schema constraints, apply order, and every reader/writer.
```bash
python scripts/preflight_migration.py <table>   # live columns+nullability, REAL index/constraint
                                                 # names, and every repo reader/writer of the table
```
- **Apply order (expand/contract):** a reader/writer must stop touching a column before it is
  dropped; a NOT-NULL column needs `DROP NOT NULL` before a writer can omit it. Split the
  migration around the deploy accordingly.
- **Every writer**, not just the file in your diff — grep the whole repo (the preflight does this).
- **Real identifiers** — index/constraint names come from `pg_indexes`/`pg_constraint`, never guessed.
- **Derived tables** (`knowledge_chunked`): validate the change on BOTH it and `knowledge`.
- No prod mutation without sign-off + a read-only dry run + a hand-reviewed retire/demote list.
- A schema change with no preflight output on the record is not a validated change — the `pm`
  reviewer's operational rubric BLOCKs it.

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

---

## Session handoff

For multi-session continuity, always start with:

1. `docs/HANDOFF.md` — latest session state, validation results, risks, and next three actions
2. `docs/OPENBRAIN_NEXT_STEPS.md` — canonical backlog

Do not assume prior session state. Read `docs/HANDOFF.md` before making any changes.

---

## Dependency profile — deployment vs local

Vercel API functions use a minimal dependency set in `requirements.txt` to stay within
Lambda install limits. Do not add heavy dependencies here.

Local/CLI development with full model tooling:

```bash
pip install -r requirements-full.txt
```

`requirements-full.txt` includes optional tooling for local ingestion experiments.
Do not add `requirements-full.txt` dependencies to `requirements.txt`.

---

## Artifact hygiene

If generated local artifacts are created during manual ingestion experiments,
**do not commit them to git.**

If local artifacts are modified by ingestion or CLI testing, restore from HEAD before committing:

```bash
git restore -- <artifact_path>
```

---

## Security incident log

Credentials have slipped into committed files twice. Both were caught and remediated,
but the pattern must not repeat.

| Date | File | What happened | Remediated |
|------|------|---------------|------------|
| 2026-05-01 | `docs/MCP_SETUP.md` | Real token committed in setup guide | Same session — replaced with placeholder |
| 2026-05-06 | `CURRENT_STATE.md` (ai-engineering-plan) | Proxmox API token pasted in planning doc | Rotated and redacted |

**Root cause:** Multiple agents (Claude Code + ChatGPT) contributing to the same codebase
with different constraint models. Neither agent caught what the other introduced.

**Standing rule:** Before committing any doc or config file, run:

```bash
git diff --staged
```

Review every line. If any real token, password, API key, or internal hostname appears — stop,
replace with a placeholder, and commit the placeholder. The pre-commit hook catches some cases
but is not a substitute for manual review of generated content.
