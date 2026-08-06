# ADR-020: Schema-change dependency gate — no drop without checking the dependents Postgres won't

**Status:** Accepted (mechanical gate shipped: `scripts/preflight_migration.py` trigger/function
enumeration + `scripts/chunk_integrity_check.py` runtime backstop, PR #101)
**Date:** 2026-08-06
**Relates to:** ADR-019 (sibling gate — 019 catches *capability without a caller*; 020 catches
its mirror image, *a dependent left dangling by a schema change*). ADR-017/018a (the migration
sequence — 005/007 — that produced the motivating case). Extends the CLAUDE.md schema-change
process control (`sql_trial.py` hard-gate + `preflight_migration.py` + expand/contract order).

## Context — the migration that broke search for six days

Migration 005 created `public.knowledge_chunked` as a full clone of `knowledge`, with its own
`status`/`system`/`tags` columns and a `BEFORE INSERT` trigger,
`validate_knowledge_chunked_insert()`, whose PL/pgSQL body reads `NEW.status` / `NEW.system` /
`NEW.tags` to enforce a per-document duplicate-current guard.

Migration 007 (ADR-018a item 3) made the chunk store content-only: metadata now JOINs from the
parent `knowledge` row, so 007 **dropped** `status`/`system`/`tags` from `knowledge_chunked`. It
correctly found and handled the dependents Postgres *tracks* — it dropped the indexes on those
columns, and (caught at apply time, not in preflight) the RLS policies gated on `status`. It did
**not** touch the trigger function, which still read the now-dropped columns.

The drop succeeded with no error. From 2026-08-01 every `INSERT` into `knowledge_chunked` raised
`record "new" has no field "status"` — but the ingest dual-write (`_dual_write_chunks`) swallows
chunk-write errors *by design* (a chunk failure must never fail the canonical parent write). So
ingest returned `accepted` truthfully, the parent row committed, and the chunk silently never
landed. Retrieval reads the chunked store, so six days of notes (4 `current` rows) were invisible
to search. It was found only when a downstream consumer noticed missing content — the worst
detection latency, months-of-019 territory compressed into a week only because the corpus is small.

**The root cause is one specific Postgres property, and it is the whole ADR:**

> A PL/pgSQL function body is stored as **opaque text** and its column references are resolved
> only at **execute time**. Postgres records **no catalog dependency** from a function body to
> the columns it names. So `ALTER TABLE ... DROP COLUMN` succeeds silently even when a trigger
> function reads that column; the failure surfaces at the next write, not at the drop.

This is deliberate on Postgres's part (late binding is what lets you drop-and-recreate inside one
transaction). It means the "the database will stop me if something depends on it" instinct — which
is *true* for indexes, foreign keys, views, generated columns, and RLS policies, and which 007's
author leaned on and had reinforced when the RLS policy blocked the drop — has a hole exactly the
shape of a function/trigger body. **The one dependency class that fails silently is the one class
no automated check in the repo was looking at.** `preflight_migration.py` enumerated columns,
index/constraint names, RLS policies, and code readers/writers — but not triggers or the functions
they call.

## Decision

Catch this dependency class **in planning**, mechanically, before the migration is authored — not
at apply time (007's RLS near-miss) and not at runtime (this incident). Three parts, mechanical
first, matching ADR-019's shape.

### 1. Classify dependents by whether Postgres protects them

| dependent of a column | Postgres catalog dependency? | how it fails on DROP |
|---|---|---|
| index, FK, view, generated col, `CHECK`/`UNIQUE` constraint | **yes** — tracked | DROP blocks or CASCADEs — **loud** |
| RLS policy predicate | **yes** — tracked | DROP blocks — **loud** (007 hit this) |
| **PL/pgSQL function / trigger body** | **NO** — opaque text, late-bound | DROP succeeds; **errors at next write — silent** |
| application code (readers/writers) | none (external) | errors at runtime — covered by `code_refs` |

Only the third row is the trap: loud failures are self-correcting, silent ones are not. The gate
targets it specifically.

### 2. The mechanical gate (load-bearing) — preflight enumerates trigger/function column reads

`scripts/preflight_migration.py` now lists every trigger on the table, the function each fires,
and **which of the table's columns each function body references** (parsed from `NEW.`/`OLD.`
field access), printing a `SILENT breakage` warning per referenced column. Run before authoring
**any** `DROP COLUMN` / `RENAME COLUMN` / column type change:

- A column being dropped that appears in a function body is a **hard STOP**. The same migration
  must update or drop that function/trigger — an expand/contract step, exactly as a reader/writer
  must stop touching a column before it is dropped.
- Preflight output is **attached to the migration**, same standing as the `sql_trial.py` output
  the CLAUDE.md hard-gate already requires. A column-affecting migration with no preflight
  trigger-section on the record is not a validated migration — the PM reviewer (ADR-019 §3) BLOCKs
  it, and its STOP comes from the mechanical output, not its own judgment (ADR-019's design
  constraint).

Run today against `public.knowledge`, the gate flags `status`/`system`/`tags`/`supersedes_id` as
drop-hazards for three live functions — i.e. it would have caught 007 at authoring time.

### 3. The runtime backstop (defense in depth, not the gate) — `chunk_integrity_check.py`

Two live invariants, wired into `smoke_checks.py` (pre-merge) and runnable standalone:
(1) **no trigger function references a column absent from its table** — the general form of this
incident, caught the moment a drop orphans a body, before a write fails; (2) **zero unchunked
`current` rows** — the effect-side check for the specific silent-swallow symptom. This is the net
under the gate, not a substitute for it: the ADR's goal is planning-phase detection, so the
preflight gate is load-bearing and the integrity check exists so a slip is caught in hours, not
days.

## Consequences

- **Positive:** the silent dependency class is now visible before a migration is written; the
  preflight artifact requirement extends the existing `sql_trial` discipline to cover it; a slip
  past the gate is caught pre-merge by smoke and in-hours by the integrity check. The trap that
  cost six days of invisible ingests cannot recur silently.
- **Cost:** the trigger/function parse is a `NEW.`/`OLD.` regex, not a real PL/pgSQL parser — it
  will not catch a column referenced via dynamic SQL (`EXECUTE format(...)`) or read from another
  table inside the body. Those are rarer and noisier; the regex covers the common trigger-guard
  shape that actually bit us. Revisit if a dynamic-SQL body ever hides a reference.
- **Enforcement moved off the author.** The weak version of this gate ("works only if the author
  runs it") is closed by the PM reviewer persona (ADR-019 §3, `home-lab/.claude/agents/pm.md`),
  which now **runs the preflight itself from the diff** — it does not accept an attached artifact
  (stale/forgeable) — and treats a dropped column that appears in a trigger/function body as a hard
  STOP. It is a **project-init precondition** for any column DROP/RENAME/retype, not only a
  merge-time check: catching it at planning is the point, review is the backstop. And the
  belt-and-suspenders is now built: `scripts/pre-commit` §3 refuses a migration diff that
  drops/renames a column or table unless a co-committed `<migration>.preflight.txt` artifact is
  present (static + offline-safe — at commit time the migration is not yet applied, so a live scan
  would read stale state; the artifact is the reviewable evidence the author ran the scan). Three
  independent layers now: author ritual (hook), planning/review gate (PM runs it), runtime net
  (integrity check in smoke).

## Not doing

- **Relying on Postgres to block it.** It structurally won't for function bodies — that is the
  entire premise. `RESTRICT`/dependency errors cover the other dependent classes, not this one.
- **A full PL/pgSQL dependency analyzer.** Overkill for a personal-scale schema; the curated
  `NEW.`/`OLD.` scan covers the real-world trigger-guard pattern. Mirrors ADR-019's "curated list
  beats a blind scan" call.
- **Rewriting the offending trigger to survive the drop.** For the motivating case the guard was
  *dead*, not broken — its invariant (one current document per `(system, component)`) is already
  owned by the parent's `one_current_per_component` unique index + `validate_knowledge_insert()`,
  which fire before any chunk write. Migration 011 drops it. Re-adding the columns would reopen the
  metadata-drift bug 007 closed; a JOIN-to-parent rewrite would re-check a parent-owned invariant.
