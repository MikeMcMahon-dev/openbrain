# OpenBrain Handoff

## Current State (2026-05-01)

- Vercel app live: `https://openbrain-rouge.vercel.app/`
- Supabase primary storage (`edljijurbmcupawnjpfx`), Transaction pooler (port 6543) in use
- `public.thoughts`: 668+ rows — `mike.mcmahon67`, `snapple01`, `anneliesepaige` (all confirmed active)
- Obsidian vault corpus imported and current
- `vault/` is a symlink: `vault -> /Users/mmcmahon/Library/Mobile Documents/iCloud~md~obsidian/Documents/Shared Vault`
- Embeddings via OpenRouter (`text-embedding-3-small`) using `OPENROUTER_API_KEY`
- **Claude.ai native MCP connector is live** — OAuth 2.0 flow complete, all 4 tools available
- **SUPABASE_DB_URL** lives in Vercel **Shared Variables** (not project-level env vars) — trailing `"` was fixed 2026-05-01

## Last Successful Validation (2026-05-01)

- Live smoke checks: **30/30** (includes 4 OAuth flow cases)
- Vault queries confirmed returning real content: `kubernetes` medium, `Talos` high, `openbrain` medium
- OAuth flow end-to-end verified: discovery → authorize → token exchange → MCP tool calls
- SSH commit signing working for `claude` OS user — commits show "Verified" on GitHub
- All three Custom GPTs still live (not re-validated this session but DB confirms active writes)

## Identity Bridge

Three per-user bearer tokens map to owner strings via `OPENBRAIN_TOKEN_OWNER_MAP` in Vercel:

| User | Owner string | Token (see .env.local) |
|------|-------------|------------------------|
| Mike | `mike.mcmahon67` | `OPENBRAIN_TOOL_ACCESS_TOKEN` (also shared admin fallback) |
| Beth | `snapple01` | per-user token |
| Annie | `anneliesepaige` | per-user token |

Token → owner resolution happens in `api/chatgpt.py:_require_tool_auth()` and is injected as `x-openbrain-owner` before core logic runs.

**Tokens were rotated 2026-05-01.** `.env.local` and Vercel are in sync. Custom GPT configs were updated manually by Mike.

## Claude.ai MCP Connector

Live at `https://openbrain-rouge.vercel.app/mcp/messages` — HTTP+SSE transport, JSON-RPC 2.0.

OAuth 2.0 endpoints (all live):
- `GET /.well-known/oauth-authorization-server` — discovery
- `GET /authorize` — PKCE authorization code flow, redirects to `claude.ai` callback
- `POST /token` — exchanges code for bearer token

Implementation: `api/oauth.py` — stateless HMAC-signed codes, 5-minute TTL, no DB required.

**Known quirk**: `SUPABASE_DB_URL` is in Vercel Shared Variables, not project env vars. It won't appear in the project-level env var list in dashboard or API. Check Shared Variables when troubleshooting DB connectivity.

## Claude Code MCP Server (stdio)

`mcp_server/openbrain.py` — stdio MCP server exposing four tools:
- `openbrain_query`
- `openbrain_ingest`
- `openbrain_generate_quiz`
- `openbrain_generate_flashcards`

Registered via `.mcp.json` at project root. Reads token from `.env.local`, calls Vercel over HTTP.

## Custom GPTs

Three family Custom GPTs live in ChatGPT:
- Each uses the OpenAPI 3.1.0 spec at `docs/CUSTOM_GPT_ACTION_SPEC.yaml`
- System prompts in `docs/gpt_instructions/`
- Authentication: Bearer token (per-user, from token map above)
- Last full validation: 2026-03-21 (DB confirms continued active use)

## Claude Code Skills (Slash Commands)

Two project-level skills are defined in `~/home-lab/claude-session-controls/commands/` and are always available in Claude Code sessions:

### `/commit`
Enforces the feature-branch-only workflow:
1. Checks current branch — creates a feature branch if on `main`
2. Stages specific files by name (never `git add -A`)
3. Commits with a *why*-focused message + `Co-Authored-By` trailer
4. Pushes to the feature branch
5. Opens a PR via `gh pr create`

**Hard rule**: never commits or pushes directly to `main`.

### `/wrap`
End-of-session ritual — run before closing any session:
1. `/commit` any open work
2. `labtime stop` with session summary (updates `lab-time.csv`)
3. Update `ai-engineering-plan/CURRENT_STATE.md` if meaningful progress
4. Ingest a session summary to OpenBrain via `openbrain_ingest` (source: text, subject: session-context, topic: session-wrap) — primary defense against context loss
5. Update `docs/OPENBRAIN_NEXT_STEPS.md` with any new backlog items

The labtime CSV path for the `claude` user: `/Users/Shared/home-lab/lab-time.csv`

## `claude` OS User Setup

The `claude` macOS user runs Claude Code sessions. Configured 2026-05-01:
- SSH signing key: `~/.ssh/id_ed25519_signing` (ed25519)
- git config: `gpg.format=ssh`, `commit.gpgsign=true`, `user.email=claude-code@mikemcmahon.dev`
- Signing key registered on CC-mcmahon-dev GitHub account
- Pre-commit hook wired via `make dev-install` (installs `requirements-dev.txt` + `core.hooksPath=scripts`)
- `gh` CLI authenticated as CC-mcmahon-dev with repo scope

## Known Gaps / Next Session

- **`/health` DB connectivity check** — currently returns 200 even when DB is broken; needs `SELECT 1` with non-200 on failure
- **Canary ingest→query smoke test** — no test currently verifies that write→retrieve round-trip works; silent DB failures go undetected
- **SafeIngest regex** — false positive on "granted" in biology content (April 2026 investigation, fix not applied)
- **Duplicate nightly session reports** — Vercel Hobby tier retry behavior; investigate log timing when next report fires

## Environment / Command Notes

- Local smoke: `.venv/bin/python scripts/smoke_checks.py`
- Live smoke: `.venv/bin/python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app`
- Full ingest: `.venv/bin/python scripts/ingest.py`
- Dev setup: `make dev-install` (installs dev deps + wires pre-commit hook)
- Default owner: `mike.mcmahon67` / default tenant: `family`
- `upload_target/personal/` — drop reference docs here for ingestion. Do NOT use `vault/` (iCloud symlink)
- **Git rule**: all changes via feature branch + PR, never direct to main; `make install-hooks` enforces it

## Vercel Gotchas

- `SUPABASE_DB_URL` is in **Shared Variables** — won't show in project env var list
- Env var values pasted with shell quotes (`"value"`) store the quotes literally; always paste raw URL
- Env var changes require a redeploy to take effect
- "Require Verified Commits" is enabled — unsigned commits block preview deployments (claude user now signs)

## Recent PRs (2026-05-01 session)

- `#34` — pre-commit guardrails (token scan + branch protection)
- `#35` — MCP `/mcp/messages` route in vercel.json
- `#36` — MCP `notifications/initialized` handling
- `#37` — OAuth 2.0 server (`api/oauth.py`)
- `#38` — fix `queryStringParameters` key in `handle_authorize`
- `#39` — fix URL-decode query params in `index.py` (`redirect_uri` was arriving encoded)
- `#40` — lint fixes + `make dev-install` target
- `#41` — session docs (NEXT_STEPS + this file)
