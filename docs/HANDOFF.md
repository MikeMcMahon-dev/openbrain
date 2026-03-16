# OpenBrain Handoff

## Current State
- Vercel app is live and stabilized at: `https://openbrain-rouge.vercel.app/`
- Supabase is primary storage (`edljijurbmcupawnjpfx`), Chroma legacy path retired.
- Ingestion and API pre-flight safeguards are implemented.
- Commits are cleanly pushed on `main`.

## Last Successful Validation
- Local smoke checks pass.
- Live smoke checks pass at `https://openbrain-rouge.vercel.app`.
- Ingest idempotency checks are in place and passing (pre/post ingest row counts stable).
- Latest handoff commit: `be6f80c`.

## Current Known Risks / Open Items
- Chat/agent connector is still the next major functional milestone.
- `api/chatgpt.py` should remain a thin platform adapter, not the core contract.
- Need user/tenant identity bridge finalized for agent calls (owner/tenant header mapping).
- Keep preflight gating behavior visible to families/admins so blocked runs are obvious.

## Next Actions
1. Validate one manual Obsidian import path end-to-end:
   - check preflight includes existing-row count and `preflight.status = ok`
   - rerun same source to confirm idempotency.
2. Finalize agent-agnostic communication layer:
   - define shared request/response contract
   - implement minimal auth + per-request tenant scoping
   - keep `api/chatgpt.py` as thin adapter.
3. Run final production smoke (`make smoke-live`) before broader family rollout.

## Environment / Command Notes
- Useful smoke run:
  - `python scripts/smoke_checks.py`
  - `python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app`
- Useful idempotency run:
  - `python scripts/smoke_checks.py --idempotency-source /tmp/openbrain-single/focus.md --idempotency-owner mike.mcmahon67`

