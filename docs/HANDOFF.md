# OpenBrain Handoff

## Current State (2026-03-21)

- Vercel app live: `https://openbrain-rouge.vercel.app/`
- Supabase primary storage (`edljijurbmcupawnjpfx`), Transaction pooler (port 6543) in use
- `public.thoughts`: 519 rows — `mike.mcmahon67` (516), `snapple01` (2), `anneliesepaige` (1)
- Obsidian vault corpus imported and current
- `vault/` is a symlink: `vault -> /Users/mmcmahon/Library/Mobile Documents/iCloud~md~obsidian/Documents/Shared Vault`
- Embeddings via OpenRouter (`text-embedding-3-small`) using `OPENROUTER_API_KEY`

## Last Successful Validation (2026-03-21)

- Local smoke checks pass (all routes 200, auth gate working)
- Live smoke checks pass (22/22 cases including 401 rejection test)
- MCP server live in Claude Code: `mcp_server/openbrain.py` via `.mcp.json`
- DB cleanup complete: no orphaned owners, no duplicate rows

## Identity Bridge

Three per-user bearer tokens map to owner strings via `OPENBRAIN_TOKEN_OWNER_MAP` in Vercel:

| User | Owner string | Token (see .env.local) |
|------|-------------|------------------------|
| Mike | `mike.mcmahon67` | `OPENBRAIN_TOOL_ACCESS_TOKEN` (also shared admin fallback) |
| Beth | `snapple01` | per-user token |
| Annie | `anneliesepaige` | per-user token |

Token → owner resolution happens in `api/chatgpt.py:_require_tool_auth()` and is injected as `x-openbrain-owner` before the request hits core logic.

## Custom GPTs

Three family Custom GPTs configured in ChatGPT:
- Each uses the OpenAPI 3.1.0 spec at `docs/CUSTOM_GPT_ACTION_SPEC.yaml`
- System prompts in `docs/gpt_instructions/`
- Authentication: Bearer token (per-user, from token map above)
- GPT URLs to be documented in brain once all three confirmed

## MCP Server (Claude Code Integration)

`mcp_server/openbrain.py` — stdio MCP server exposing four tools:
- `openbrain_query`
- `openbrain_ingest`
- `openbrain_generate_quiz`
- `openbrain_generate_flashcards`

Registered via `.mcp.json` at project root. Reads token from `.env.local`, calls Vercel over HTTP.

## Open Items

- Annie's school content import (deferred — Spring Break; coordinate with wife)
- Custom GPT URLs — ingest into brain once all three confirmed working
- DB health automation — planned as K8s CronJob (separate project)

## Environment / Command Notes

- Local smoke: `.venv/bin/python scripts/smoke_checks.py`
- Live smoke: `.venv/bin/python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app`
- Full ingest: `.venv/bin/python scripts/ingest.py`
- Default owner: `mike.mcmahon67` / default tenant: `family`
- `gh` CLI installed — use for PR creation going forward
- **Git rule**: all changes via feature branch + PR, never direct to main

## Recent Commits

- `83f0160` — Add OpenBrain MCP server for Claude Code integration
- `6d1a48a` — Add text source_type support and word-count guard for GPT ingest
- `acdd555` — Remove tokens from GPT instruction files, exclude gpt_instructions from brain ingest
- `4ad89a8` — Add per-user GPT instruction files and fix token owner map
- `f679859` — Add per-user token → owner mapping for family identity bridge
