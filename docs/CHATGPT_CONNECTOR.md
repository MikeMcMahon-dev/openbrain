# ChatGPT Connector

Two integrations exist. The **MCP connector** (below) is the current path — ChatGPT now speaks
MCP directly and each family member signs in with their own passphrase. The **Custom GPT
Actions** integration further down is the original one and still works.

## MCP connector (ChatGPT Developer mode) — 2026-09-13

ChatGPT connects to the same MCP endpoint claude.ai uses. Verified end to end before this
was written: OpenAI's MCP client lists our tools and calls `search` over plain POST
`/mcp/messages` (no SSE needed), and the OAuth flow below runs against `api/index.py`.

### One-time server setup (Mike)

1. Mint each owner's passphrase hash. The passphrase is typed at a hidden prompt and never
   stored; only the PBKDF2 hash goes into the env var.
   ```bash
   .venv/bin/python scripts/oauth_passphrase.py mike.mcmahon67
   .venv/bin/python scripts/oauth_passphrase.py anneliesepaige --merge-into '<JSON from previous step>'
   ```
2. In Vercel → Project → Settings → Environment Variables (Production):
   - `OPENBRAIN_OAUTH_PASSPHRASES` = the printed JSON `{"owner": "pbkdf2-sha256$…"}`
   - `OPENBRAIN_OAUTH_SIGNING_SECRET` = a long random string (`openssl rand -hex 32`).
     Optional — falls back to `OPENBRAIN_TOOL_ACCESS_TOKEN` — but a dedicated secret means
     rotating it revokes every OAuth-issued token without touching the static bearers.
3. Redeploy. Confirm `https://openbrain-rouge.vercel.app/.well-known/oauth-authorization-server`
   now lists a `registration_endpoint`.

### Per-user setup (each family member, in their own ChatGPT)

1. ChatGPT → **Settings → Apps & Connectors → Advanced → Developer mode** (on).
2. **Create** connector:
   - **Name:** OpenBrain
   - **MCP server URL:** `https://openbrain-rouge.vercel.app/mcp/messages`
   - **Authentication:** OAuth (leave client id/secret blank — the server registers the
     client automatically)
3. ChatGPT opens the OpenBrain sign-in page. Enter your **username** (your owner id:
   `mike.mcmahon67`, `snapple01`, `anneliesepaige`) and your passphrase. Access lasts 90 days.
4. In a chat, enable the OpenBrain connector and ask: *"Search my brain for …"*.

### How the flow works

| Step | Endpoint | What happens |
|---|---|---|
| discovery | `GET /.well-known/oauth-protected-resource`, `GET /.well-known/oauth-authorization-server` | ChatGPT finds the authorization server (also advertised in the `WWW-Authenticate` header on a 401 from `/mcp/messages`) |
| register | `POST /register` | RFC 7591 dynamic registration. The returned `client_id` is a signed blob of the registered redirect URIs — no client table |
| login | `GET /authorize` → form → `POST /authorize` | Passphrase checked against `OPENBRAIN_OAUTH_PASSPHRASES`; only then is a 5-minute code issued, only to a registered redirect |
| token | `POST /token` | PKCE (S256) verified; a signed 90-day token bound to (owner, client) is returned |
| tools | `POST /mcp/messages` | `Authorization: Bearer obt_…` resolves to the owner; every tool is owner-scoped |

Client id metadata documents (`client_id` = an https URL naming itself) are accepted too.
The hand-configured claude.ai connector (client id = owner name) keeps working but is
restricted to `claude.ai` / `claude.com` redirect hosts.

### Revoking / rotating

- One person's passphrase: mint a new hash, replace their entry, redeploy.
- One person's OAuth access entirely: remove their entry (blocks new sign-ins) and rotate
  `OPENBRAIN_OAUTH_SIGNING_SECRET` (kills issued tokens — for everyone; tokens are stateless).
- Static bearers in `OPENBRAIN_TOKEN_OWNER_MAP` are unaffected by any of this.

### Not done yet (optional polish)

ChatGPT's citation feature wants `search` results to carry `url` and `fetch` to take a single
`id`; ours return text content with `ids[]`. Tools work without it; citations just render as
ordinary tool output.

---

## Custom GPT Actions (original integration)

This section describes the ChatGPT Custom GPT integration with OpenBrain.

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
