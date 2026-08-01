# Handoff — OpenBrain P3: transition records (2026-08-01)

Read this + `docs/decisions/ADR-018_a-supersession-transition-records.md` (rev.6, the governing
spec) before touching P3. P1 and P2 are **done and applied**; this sets up P3.

## Where we are — P1 + P2 landed and verified

- **P1 recency net — live.** `OPENBRAIN_RECENCY_HALFLIFE_DAYS=90`, allowlisted to Network/K8s/
  Security, exempts `durable`-tagged + component-keyed rows. Confirmed decaying old unkeyed rows.
- **P2 metadata ownership — COMPLETE (applied 2026-08-01).** `knowledge_chunked` is now
  **content-only**: `id, content, embedding, document_id, chunk_index, heading, valid_from,
  valid_until, supersedes_id, ingest_id, source, created_by, created_at`. Every mutable metadata
  facet (system/status/component_key/tags/domain/environment) JOINs from the parent `knowledge`
  row on `document_id` — drift is now structurally impossible, not monitored.
  - `component_key` has a writer on the parent INSERT; retrieval reads it (tag fallback).
  - `system` is a required, validated ingest param when a `component:*` is present (#82).
  - 006 `component_requires_system` CHECK is **validated** (fully enforced).
  - Migrations applied: 006 §A–D + re-key, 007 §A/§B (+ 2 RLS-policy drops), 006 §E (+ adr-018
    de-registration). Verified end state: **735 current / 67 historical / 5 superseded**, invariants
    green, live smoke exit 0.

## Process controls now MANDATORY (do not skip — this is why P2 was bumpy)

These are live in `CLAUDE.md` + ADR item 10 and are non-negotiable for every P3 DB step:

1. **`scripts/sql_trial.py "<sql>"` — HARD GATE (10e).** Every prod statement runs in `BEGIN…
   ROLLBACK` first; **no SQL reaches Mike without its trial output attached, and Mike rejects any
   that arrives without it.** P2 hit two live errors (RLS-policy dep, CHECK violation) *because*
   this was skipped. Do not trust a narrow audit/count as a substitute — trial the real statement.
2. **`scripts/preflight_migration.py <table>`** before authoring any ALTER/DROP — dumps live
   columns+nullability, REAL index/constraint names, **RLS policies**, and every repo reader/writer.
3. **Expand/contract apply order** — a reader/writer stops touching a column before it's dropped;
   NOT-NULL columns get `DROP NOT NULL` first; split the migration around the deploy.
4. **No prod mutation** without sign-off + a read-only dry run + a hand-reviewed list. The `pm`
   reviewer's operational rubric BLOCKs a schema change with no preflight/trial evidence.

## P3 — the plan (ADR §Decision items 1, 2, 5)

Goal: make retirement an **append-only transition record**, and `knowledge.status` a **projection
maintained by trigger** — replacing the in-place `auto_supersede` UPDATE.

1. **`supersession_events`** — new table, append-only, immutable (no UPDATE/DELETE):
   `superseded_id FK→knowledge.id`, `superseding_id FK→knowledge.id` (nullable: expiry has no
   successor), `occurred_at timestamptz` (system time), `reason_code enum` (explicit |
   component_collision | contradiction_confirmed | ttl_expiry | manual | migration), `reason_note`,
   `actor`, `method enum` (agent | human | job | backfill).
2. **Backfill** the existing supersession chains as `reason_code='migration'` — **5 rows** carry a
   `supersedes_id` today (ADR said 4; grew by one). These seed the event log.
3. **Projection** — a trigger is the *only* writer of `knowledge.status`; nothing else touches it.
   `knowledge_chunked` already carries no status (P2), so chunks inherit via the join — no cascade.
4. **Guard** — a `DEFERRABLE INITIALLY DEFERRED` constraint trigger checked at COMMIT (the FK
   `superseding_id → knowledge.id` needs the new row to exist first, so a plain BEFORE INSERT can't
   work). The write path wraps knowledge-insert + event-insert in ONE transaction.
5. **Reconciliation** — a nightly job proving stored `knowledge.status` matches the
   transitions-derived status. Drift is a bug (alert), not a warning. Recovery is **replay from the
   event log, not restore** — `supersession_events` is the truth, status is rebuildable.
6. **Retire `auto_supersede`** — the current tag-keyed in-place UPDATE in `api/knowledge_ingest.py`
   is superseded once the projection takes over.

## Preconditions / open items to clear as part of P3

- **Reconcile `environment='Archive'` first — 9 rows.** A retirement-adjacent concept predates this
  work; establish what those 9 are and whether `Archive` overlaps `historical`/`superseded`, so P3
  extends rather than duplicates. (ADR §Scope honesty.)
- **`auto_supersede` still keys on the `component:*` tag, not the `component_key` column** (deferred
  P2 tail). Correct/robust while the tag stays on `knowledge`; P3 retires the whole mechanism, so
  the column-switch can happen inside P3 rather than separately.
- **Test harness** — Suites A/B need a real **ephemeral schema** (not a prod tenant): fixtures
  F2/F7/F10 are deliberately-invalid rows; a missed teardown turns a fixture contradiction into a
  real answer. Decide the ephemeral-schema setup before Suite B.
- **`occurred_at` (system time) vs `valid_until` (valid time)** are *not* the same column — a
  backdated retirement sets `valid_until` earlier than `occurred_at`. Pin this across P3/P5.

## Parallel open item (not P3, but live)

- **P1.5 Durable AMBER.** The recency net is live with **0 durable-tagged members** — ~12
  foundational non-keyed Network/K8s/Security docs are exposed to decay. Mike marks the durable
  set; I produce the candidate list (reason per row). Open question: should `component_key` imply
  `durable`? (A living doc is the current-state record for its component — sinking one is always
  wrong; if yes, keyed rows are structurally exempt and future ones inherit it.)

## Housekeeping

- **Lint pass 2 pending** — ~98 `E501` line-wraps + 4 minor (2 unused vars, 1 import placement, 1
  semicolon). #88 did pass 1 (a real bug: missing `BeautifulSoup` import, + 50 autofixes).
- **Gotcha:** `git reset --hard origin/main` without a fresh `git fetch` lands on a *stale* main —
  fetch first (bit me writing this handoff).
- ADRs are decided in git, ingested to OpenBrain ONCE — do NOT re-ingest in-flight ADRs (that's how
  the `component:adr-018` CHECK violators were created).
