# ChatGPT Connector (Complete)

This document describes the live ChatGPT Custom GPT integration with OpenBrain.

## Live Routes

All routes live at `https://openbrain-rouge.vercel.app`:

| Route | Purpose |
|-------|---------|
| `POST /openbrain_query` | Hybrid keyword + vector search |
| `POST /openbrain_generate_quiz` | Quiz generation from vault |
| `POST /openbrain_generate_flashcards` | Flashcard generation from vault |
| `POST /openbrain_ingest` | Save new content to vault |

`/tools/*` variants of each route also exist for compatibility.

## Authentication

Each Custom GPT sends `Authorization: Bearer <token>` where token is per-user.
`OPENBRAIN_TOKEN_OWNER_MAP` in Vercel resolves token → owner string, which is injected
as `x-openbrain-owner` before the request hits core logic.

| User | Owner | Token source |
|------|-------|-------------|
| Mike | `mike.mcmahon67` | `OPENBRAIN_TOOL_ACCESS_TOKEN` (also admin fallback) |
| Beth | `snapple01` | per-user token in token map |
| Annie | `anneliesepaige` | per-user token in token map |

## Action Spec

OpenAPI 3.1.0 spec: `docs/CUSTOM_GPT_ACTION_SPEC.yaml`
Paste directly into ChatGPT → Configure → Create new action → Schema.

## System Prompts

Per-user system prompts in `docs/gpt_instructions/`:
- `mike_mcmahon67.md` — technical, direct
- `snapple01.md` — non-technical adult, friendly
- `anneliesepaige.md` — study-focused, age-appropriate guardrails

## Text Ingest Behaviour

- `source_type: text` — paste content directly as `source` field
- Content under 6000 words: single call, status `accepted`
- Content over 6000 words: server returns 413, GPT splits into ≤1500 word sections and re-submits
- Threshold configurable via `OPENBRAIN_TEXT_INGEST_MAX_WORDS` Vercel env var

## Payload Envelope

`api/chatgpt.py` resolves payload from three envelope styles:
- `tool_input` key (ChatGPT tool_input style)
- `input` key (generic)
- `arguments` key (function-call style)
- Falls back to raw payload if none match
