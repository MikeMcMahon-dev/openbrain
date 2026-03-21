# OpenBrain Handoff

## Current State
- Vercel app is live at: `https://openbrain-rouge.vercel.app/`
- Supabase is primary storage (`edljijurbmcupawnjpfx`), Chroma legacy path retired.
- 775 rows in `public.thoughts`, all with embeddings, under tenant `family`.
- Obsidian vault corpus confirmed imported (multiple successful runs of `scripts/ingest.py`).
- `vault/` is a symlink: `vault -> /Users/mmcmahon/Library/Mobile Documents/iCloud~md~obsidian/Documents/Shared Vault`
- Embeddings use OpenRouter (`text-embedding-3-small`) via `OPENROUTER_API_KEY` — not direct OpenAI.
- `OPENBRAIN_TOOL_ACCESS_TOKEN` is set in Vercel. `/openbrain_*` and `/tools/openbrain_*` routes are wired, deployed, and auth-gated.

## Last Successful Validation (2026-03-20)
- Local smoke checks pass (all routes 200, query returns vault content for owner `mike.mcmahon67`).
- Live auth gate confirmed: wrong token → 401, correct token → 200.
- Ingest idempotency checks are in place and passing (pre/post ingest row counts stable).
- Latest handoff commit: `3c116c3`.

## Current Known Blocker
**Vercel read path returns empty results.**
- Root cause: `SUPABASE_DB_URL` resolves to an IPv6 address; Vercel serverless is IPv4-only.
- Error: `connection to server at "2600:..." failed: Cannot assign requested address`
- Fix: replace `SUPABASE_DB_URL` in Vercel env (and `.env.local`) with the Supabase
  **Transaction pooler** connection string (port `6543`, `aws-0-us-east-1.pooler.supabase.com`).
  Find it at: Supabase → Settings → Database → Connection string → Transaction mode.
- After updating the env var, redeploy Vercel.

## Open Items
- Custom GPT action OpenAPI spec: not yet written.
- Identity strategy: Slack user_id as canonical identity; per-user token mapping planned.
- RLS enforcement: scaffolded only (Phase 2).
- OPENAI_API_KEY not in Vercel (using OpenRouter instead — already working via OPENROUTER_API_KEY).

## Next Actions
1. Fix `SUPABASE_DB_URL` → Transaction pooler URL in Vercel + `.env.local`, then redeploy.
2. Smoke test live read path: `make smoke-live SMOKE_URL=https://openbrain-rouge.vercel.app`
3. Validate keyword hit ranking (query for known vault terms like "Terraform", "SELinux").
4. Write Custom GPT action OpenAPI spec (routes already live, auth already gated).
5. Configure Custom GPT action and run end-to-end: study question → flashcards → quiz.
6. Cross-tenant leak test before family rollout.

## Environment / Command Notes
- Local smoke: `.venv/bin/python scripts/smoke_checks.py`
- Live smoke: `make smoke-live SMOKE_URL=https://openbrain-rouge.vercel.app`
- Idempotency: `.venv/bin/python scripts/smoke_checks.py --idempotency-source /tmp/openbrain-single/focus.md --idempotency-owner mike.mcmahon67`
- Default owner: `mike.mcmahon67` / default tenant: `family`

