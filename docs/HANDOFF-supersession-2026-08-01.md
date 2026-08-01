# Handoff — OpenBrain supersession / temporal redesign (2026-08-01)

Read this + `docs/decisions/ADR-018` and `ADR-019` before touching retrieval or ingest.
Context for a fresh session. The work is CC↔Chat adversarial-relay (Mike relays); verify
Chat's premises against the live code/DB before agreeing.

## TL;DR — two actions activate everything already built

1. **Set `OPENBRAIN_RECENCY_HALFLIFE_DAYS=90` in Vercel.** P1 (recency net) is MERGED but
   ships OFF (defaults to 0). This one env var + the already-done durable pre-pass makes the
   measured June/July stale-ranking bug go away in prod. Nothing else needed.
2. **Apply migration `supabase/migrations/006_component_key_system.sql`** (Supabase SQL editor)
   — after blessing 2 namespace values. This is the P2 DB foundation.

## What is LIVE / MERGED

- **Chunking (ADR-017)** + retrieval fixes (recall/skim-fetch/confidence/length-penalty/
  sibling_chunks) — all merged, live. `KNOWLEDGE_TABLE=knowledge_chunked`, `COMPONENT_BOOST=2.0`,
  `CHUNK_ON_INGEST=1` set in Vercel.
- **P1 recency net (#79 merged)** — code live but DORMANT (`OPENBRAIN_RECENCY_HALFLIFE_DAYS=0`).
  Allowlisted to Network/K8s/Security; exempts `durable`-tagged + component-keyed + other domains.
  Durable pre-pass DONE: 8 foundational docs tagged `durable` (prod), `durable` registered in
  tag_vocabulary as an INTERIM lifecycle tag (removed in P2). Activate via the env var above.
- **ADR-019 completeness gate (merged)** — `scripts/capability_audit.py` + `capability_audit.allow.json`,
  wired into `make check`. Catches "capability without a caller." The `pm` reviewer persona is
  home-lab `.claude/agents/pm.md` (PR home-lab #42).

## What is STAGED (branches / open PRs — Mike merges)

| PR | branch | what |
|---|---|---|
| **#82** | `feat/p2-system-ingest` | **P2 build-half**: `system` at ingest surface + migration 006 + P0 monitor + vocab proposal |
| #81 | `chore/lint-openbrain-api` | lint sweep (24 E501s) |
| #80 | `docs/adr-018-supersession` | ADR-018 rev.4 (the decision record) |
| home-lab #42 | `chore/pm-persona` | the pm persona |
| (this) | `chore/handoff-2026-08-01` | this handoff |

## P2 — the plan and what's left

**Migration:** `supabase/migrations/006_component_key_system.sql` (on #82, NOT main). Apply via
Supabase SQL editor. Adds: `component_key` column + backfill from the `component:*` tag;
`system_vocabulary` table + validation trigger (seed = `api/canonical_systems.py`);
`CHECK (component_key IS NULL OR system IS NOT NULL)` added **NOT VALID**; `one_current_per_component`
partial unique index with **NULLS NOT DISTINCT**.

**Build-half DONE (#82):** `system` is now settable at the ingest surface (`ob_ingest --system` +
payload), REQUIRED when a `component:*` is present (rejects null-identity writes), validated vs
`api/canonical_systems.py`. Standing P0 monitor in `capability_audit` (null-system-component →0,
>1-current-per-component =0 gate).

**Apply-half — NEEDS MIKE (all prod mutations / a taxonomy call):**
1. Bless 2 namespace values for the null-system rows — proposed `FlightSim` (flightsim-hardware)
   and `MikeMcMahon-Dev` (mikemcmahon-dev-design); see `docs/P2-system-vocabulary-proposal.md`.
   Add them to BOTH `api/canonical_systems.py` and migration 006 §B.
2. Apply 006 §A–D.
3. Re-key the 2 rows: `UPDATE knowledge SET system='<value>' WHERE …` — the CHECK validates each.
4. `ALTER TABLE public.knowledge VALIDATE CONSTRAINT component_requires_system;`
Acceptance signal: `make capability-audit` shows `null-system component rows` go **2 → 0**.

**Deferred (blocked on the column existing):** refactor `auto_supersede` to read the
`component_key` column instead of the tag — would error against a missing column pre-migration.

## Beyond P2

- **P3 transition records** — `supersession_events` (append-only), `status` as materialised
  projection on `knowledge` ONLY (chunks join on `document_id`, Q1), deferrable constraint guard,
  reconciliation job. Test harness Suites A/B need a real ephemeral schema.
- **P4 contradiction detection** — candidate surfacing (same-system high-similarity current pairs).
- **P5 bitemporality** — DEFERRED/unscheduled: `valid_from` need is unmeasurable-because-unexposed
  (same trap as system), not proven absent. `valid_from` set explicitly at ingest, never `may`.
- **Reconcile `environment='Archive'`** (9 rows in use) before P2/P3 build a parallel retirement concept.

## Gotchas / rules
- **ADRs are DECIDED in git, ingested to OpenBrain ONCE.** Do NOT re-ingest an in-flight ADR —
  re-ingest creates duplicate current rows (supersession is gated on `system`, which… is exactly
  what P2 fixes). Git is the ADR source of truth.
- Migrations: Supabase SQL editor or psycopg direct-execute; NOT `supabase db push`.
- Tests: `make test` (repo-root `vault/` symlink breaks bare pytest for the `claude` user). Suite
  is 130 green + 1 xfail (the C3 boost-off harness marker — flips green + comes out when recency lands).
- No prod DB mutation without sign-off — the auto-mode classifier blocks it, correctly.
- Non-negotiable before P2/P3 touch prod rows: read-only dry run + hand-reviewed retire/demote list.
