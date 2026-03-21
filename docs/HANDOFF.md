# OpenBrain Handoff

## Current State (2026-03-21)

- Vercel app live: `https://openbrain-rouge.vercel.app/`
- Supabase primary storage (`edljijurbmcupawnjpfx`), Transaction pooler (port 6543) in use
- `public.thoughts`: ~525+ rows — `mike.mcmahon67`, `snapple01`, `anneliesepaige` (all confirmed active)
- Obsidian vault corpus imported and current
- `vault/` is a symlink: `vault -> /Users/mmcmahon/Library/Mobile Documents/iCloud~md~obsidian/Documents/Shared Vault`
- Embeddings via OpenRouter (`text-embedding-3-small`) using `OPENROUTER_API_KEY`
- **Text ingest (`source_type=text`) is now fully working** — embeds and upserts into DB via `_write_text_ingest()` in `api/_openbrain_api.py`

## Last Successful Validation (2026-03-21)

- Local smoke checks pass (all routes 200, auth gate working)
- Live smoke checks pass (22/22 cases including 401 rejection test)
- MCP server live in Claude Code: `mcp_server/openbrain.py` via `.mcp.json`
- DB cleanup complete: no orphaned owners, no duplicate rows
- Text ingest end-to-end verified: ingest via MCP → DB write → query retrieval confirmed
- All three Custom GPTs confirmed working: Mike, Beth, Annie
- Tenant isolation confirmed: Annie's content not visible to Mike's queries

## Identity Bridge

Three per-user bearer tokens map to owner strings via `OPENBRAIN_TOKEN_OWNER_MAP` in Vercel:

| User | Owner string | Token (see .env.local) |
|------|-------------|------------------------|
| Mike | `mike.mcmahon67` | `OPENBRAIN_TOOL_ACCESS_TOKEN` (also shared admin fallback) |
| Beth | `snapple01` | per-user token |
| Annie | `anneliesepaige` | per-user token |

Token → owner resolution happens in `api/chatgpt.py:_require_tool_auth()` and is injected as `x-openbrain-owner` before the request hits core logic.

## Custom GPTs

Three family Custom GPTs confirmed working in ChatGPT:
- Each uses the OpenAPI 3.1.0 spec at `docs/CUSTOM_GPT_ACTION_SPEC.yaml`
- System prompts in `docs/gpt_instructions/`
- Authentication: Bearer token (per-user, from token map above)
- All three GPTs validated end-to-end (query, ingest) on 2026-03-21
- GPT URLs to be ingested into brain (pending)

## MCP Server (Claude Code Integration)

`mcp_server/openbrain.py` — stdio MCP server exposing four tools:
- `openbrain_query`
- `openbrain_ingest`
- `openbrain_generate_quiz`
- `openbrain_generate_flashcards`

Registered via `.mcp.json` at project root. Reads token from `.env.local`, calls Vercel over HTTP.

## User Context

Mike's full profile is now in the brain (`topic: personal-history`, `topic: career`, `topic: values`, `topic: life-goals`, `topic: resume`). Key facts for next session:

- Senior infrastructure engineer, 30 years in IT, currently at NVIDIA (SRO, CIS team, GPU-as-a-Service in Slurm clusters across major cloud providers). Started December 2025.
- Near-term goal: automate NVIDIA ticket workflow (highly automatable, directly improves quality of life and health)
- K8s fluency is a shared goal: required by NVIDIA, also a 2026 Rockwell goal — "buy one, get one free"
- Family: wife Beth (knitting, not tech-heavy), daughter Annie (13, 7th grade, Christian school, struggles with Science — brain is her study tool)
- Location: Star/Boise metro, Idaho
- Interests: target shooting, overlanding/camping, 2026 F-150 Raptor, movies

## Open Items

- Annie's school content import — in progress (she's started; more to come)
- Custom GPT URLs — ingest into brain once all three confirmed
- NVIDIA ticket automation — near-term project, high personal impact
- DB health automation — planned as K8s CronJob (separate project)
- RLS enforcement — scaffolded only; Phase 2 hardening not yet applied
- Slack identity canonicalisation — future architectural goal
- vault/Personal/ created — use for dropping reference docs (resumes, context files) for ingestion via `scripts/ingest.py`

## Environment / Command Notes

- Local smoke: `.venv/bin/python scripts/smoke_checks.py`
- Live smoke: `.venv/bin/python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app`
- Full ingest: `.venv/bin/python scripts/ingest.py`
- Default owner: `mike.mcmahon67` / default tenant: `family`
- `gh` CLI installed — use for PR creation going forward
- **Git rule**: all changes via feature branch + PR, never direct to main

## Recent Commits

- `d6ef613` — Fix text ingest: introspect schema columns before INSERT (PR #7)
- `ac0f691` — Fix text ingest: actually write to DB instead of silently accepting (PR #6)
- `83f0160` — Add OpenBrain MCP server for Claude Code integration
- `6d1a48a` — Add text source_type support and word-count guard for GPT ingest
- `acdd555` — Remove tokens from GPT instruction files, exclude gpt_instructions from brain ingest
