# ADR-018: Supersession as append-only transition records

**Status:** P1 **shipped and live**. P2 **COMPLETE and applied** (2026-08-01): `knowledge_chunked`
is content-only, metadata joins from the parent, `component_key` has a writer, and 006's
`component_requires_system` CHECK is **validated**. Verified end state: content-only chunk table,
constraint validated, 807/1317 rows intact, invariants green, live smoke clean. Next up is P3
(transition records). Only P2 tail left: the `chore(lint)` sweep.
**Date:** 2026-08-01 (rev.6 — P2 applied end-to-end; expand/contract apply order + two apply-time
finds recorded: RLS-policy dependency and the adr-018 CHECK-violator artifacts)
**Relates to:** ADR-008 (living-doc identity), ADR-011 (document model), ADR-017 (chunking —
the read path this must stay consistent with), ADR-019 (deployment-completeness gate).
Supersedes the `auto_supersede` UPDATE-in-place mechanism in `api/knowledge_ingest.py`.

> **Document standard (rev.5).** This ADR has **one** Decision section. There is no
> `Resolutions` appendix. Rev.2–4 carried the design in two places — §Decision said `knowledge`,
> while §Resolutions item 1 said "join to parent, don't cascade." An implementer read §Decision,
> built single-table, and re-introduced the exact flaw §Resolutions had killed. **A governing
> decision that appears in two sections has already failed.** Superseded reasoning lives in git
> history, not in an appendix that the reader must know to consult.

---

## Context — two measured problems

### 1. Stale content outranks its own replacement

Verified against prod 2026-07-31:

- Query `Technitium DNS cluster DANE TLSA certificate trust` at `boost=1.0`: the **June
  "Technitium is healthy"** note ranks **#0**; the **July "Technitium is decommissioned"**
  authoritative doc falls to **#3**, below a superseded row. The ×2 component boost was the only
  thing hiding it, and it fired on **3 of 731 current rows** (6 real component-keyed rows as of
  writing; the count grew as this work produced tagged docs).
- **83% of current rows are temporally ungrounded** — no in-content date, no component key.
  91% of the corpus is `current` and nothing retires it, so present-tense assertions live forever.
- Supersession **UPDATEs in place**; **nothing detects** that a new record contradicts a current
  one — a human must notice, which the cutover post itself says solo operators fail at.
- RRF is **recency-blind**; `created_at` (100% coverage) was unused by ranking.

### 2. Metadata has no single source of truth — discovered mid-P2, 2026-08-01

`knowledge_chunked` is **a hand-maintained duplicate of `knowledge`'s metadata**. Production
reads from it (`KNOWLEDGE_TABLE=knowledge_chunked`), but `system`, `status`, `component_key`,
`tags`, `domain`, `environment` are copies kept in sync by imperative app code — not derived.
Sync points: `knowledge_ingest.py:340` (parent INSERT), `:85–90` (`_mirror_chunks()`, a separate
column list), `:92–99` (a second UPDATE superseding prior chunks by tag).

Anything synced by code a human must remember to update **drifts**. Three instances in nine days:

| # | Instance | Evidence |
|---|---|---|
| 1 | The ADR-017 chunking cutover | metadata mirrored by hand from the start |
| 2 | `component_key` | Migration 006 backfilled the column on **both** tables, but `component_key` is written **nowhere** in `api/` (grep is empty). Every new ingest lands `component_key = NULL` on both. Inert today only because retrieval still reads the `component:*` **tag** (`knowledge_retrieval.py:111–112`), not the column. |
| 3 | The re-key itself | `UPDATE knowledge SET system='MikeMcMahon-Dev' WHERE component_key='mikemcmahon-dev-design'` touched **`knowledge` only**. That document's chunk rows still carry the old null `system`. **The fix created fresh drift.** |

`component_key` is today's face of the root problem, not the problem. **Patching the INSERTs to
also write it adds a fourth hand-sync point to the pile we are trying to eliminate. A drift
monitor tells you you are bleeding; it does not stop it.**

---

## Decision

Everything the implementation must honour is in this section. Each item states **which tables it
touches**, because single-table reasoning is what produced instance #3 above.

### 1. `supersession_events` — the truth of what was retired

New table. Append-only, immutable (no UPDATE, no DELETE), first-class:

```
superseded_id      FK → knowledge.id
superseding_id     FK → knowledge.id   -- nullable: expiry has no successor
occurred_at        timestamptz         -- system/recorded time
reason_code        enum                -- explicit | component_collision |
                                       -- contradiction_confirmed | ttl_expiry |
                                       -- manual | migration
reason_note        text
actor              text
method             enum                -- agent | human | job | backfill
```

**Tables touched:** new table only. References `knowledge`, never `knowledge_chunked`.

### 2. `knowledge.status` is a materialised projection — and only `knowledge` has one

`status` stays a **stored column on `knowledge`**, not read-time-derived, so a partial unique
index can enforce *at most one current row per component* at the database level. Deliberate
divergence from full read-time derivation: append-only truth where it matters, indexable
projection for the uniqueness guarantee.

**`knowledge_chunked` carries no `status`.** Retrieval joins chunks to their parent on
`document_id`.

Verified against the read path: `status` **is** filtered — `_build_filter_clause` appends
`WHERE status = %s` defaulting to `'current'` (`knowledge_retrieval.py:147-150`) — but the three
status-composite indexes on `knowledge_chunked` **do not drive that filter**: HNSW drives the
vector path, a seq scan the keyword path, and at **82%** `current` the predicate is barely
selective (no filtered-HNSW pathology). So `status` is an active *filter*, not an inert column:
dropping it (item 3) means relocating the `status='current'` predicate onto the joined parent,
not deleting an unused field.

**Tables touched:** `knowledge` (status stored, projected by trigger). `knowledge_chunked`
(status column **dropped**).

### 3. All mutable metadata is owned by `knowledge` and joined at read — chunks own only content

This is the rev.5 change, and it generalises item 2 from `status` to **every** mutable metadata
column. `status` was never the only field that drifts; it was the only one we happened to
examine.

**`knowledge_chunked` stores:** `document_id`, `chunk_index`, `heading`, `content`, `embedding`,
`created_by`.

**`knowledge_chunked` does not store:** `status`, `system`, `component_key`, `tags`, `domain`,
`environment`. All are JOINed from `knowledge` on `document_id` at query time.

Rationale, stated once so it is not re-litigated:

- **A trigger makes drift enforced-against. A join makes drift impossible.** Three instances in
  nine days were each a sync point someone forgot. Removing the copies removes the category —
  option 1 (metadata-sync trigger) leaves the copies and adds machinery to defend them.
- **`created_by` stays denormalised** — immutable, so no divergence risk, and it keeps
  owner-scoping (the security-critical filter, and the highest-severity failure mode in this
  work) a single-table predicate.
- **Content and embeddings stay app-produced.** Chunking is not pure SQL: each chunk needs a
  heading-aware split and a per-chunk embedding API call. Supabase managed offers no in-DB
  embedding and `pg_cron` is off by default. Only *metadata* is a candidate for DB-side
  derivation; content never was.
- The **content dual-write must still be atomic** — a parent with no chunks is its own bug.
  Metadata simply is not part of that write any more.

**Tables touched:** `knowledge_chunked` (six metadata columns dropped, plus their indexes);
`knowledge_retrieval.py` (join to `knowledge` on `document_id` added; the `status='current'`
filter, every metadata SELECT/facet — `system`/`tags`/`domain`/`environment` — and tag-reading
for `component_key` **all move onto the joined parent**; this is the full read-path rewrite, not a
single added join).

### 4. `component_key` is a validated column, and `system` is a required ingest parameter

`component_key` is promoted from the `tags` array to a real column **on `knowledge` only**
(chunks join for it, per item 3) with a vocabulary table — today an unregistered key is accepted
silently.

`system` becomes an **explicit, required ingest parameter whenever a component key is present**.
It is settable in `write_knowledge()` but **not exposed by the ingest API or CLI**, so it is
inferred, unreliably — the same subject yielded `OpenBrain` for one write and `null` for another.
That makes a complete `(system, component)` identity **impossible to produce on purpose**.

- `CHECK (component_key IS NULL OR system IS NOT NULL)` — the **primary** guard, rejecting an
  incomplete identity at write time.
- `one_current_per_component` partial unique index with **`NULLS NOT DISTINCT`** (PG 17.6,
  confirmed) — the **secondary** belt. Standard NULL-distinctness treats two NULL systems as
  *different*, so `(NULL,'k')` and `(NULL,'k')` **fail to collide** and both rows are permitted;
  that is the gap that lets a null-system component silently duplicate.

**A write path must exist before this is called done.** `component_key` currently has a column,
a backfill, and no writer — the exact ADR-019 "capability without a caller" pattern, in this
ADR's own migration.

**Tables touched:** `knowledge` (column, CHECK, unique index, vocabulary FK);
`knowledge_chunked` (column **dropped** — added by migration 006 §A, reverted per item 3);
`api/knowledge_ingest.py` (write path); ingest API + CLI (parameter surface).

### 5. Enforcement — one writer, one guard, one reconciler

- **Guard:** a `DEFERRABLE INITIALLY DEFERRED` constraint trigger, checked at COMMIT. A plain
  `BEFORE INSERT` is impossible because `superseding_id → knowledge.id` requires the new row to
  exist first. The write path wraps knowledge-insert + event-insert in one transaction.
- **Projection:** the *only* writer of `knowledge.status`. Nothing else touches it.
- **Reconciliation:** a nightly job proving stored status matches transitions-derived status.
  Drift is a bug, not a warning.

**Tables touched:** `knowledge`, `supersession_events`.

### 6. Recency net — the estate-wide safety layer

Read-time `created_at` decay for the ~613 floaters that carry no component key and never will.
Applied **only to `Network`, `K8s`, `Security`** — an allowlist, not a denylist, scoped to the
domains where the failure was measured. `durable`-tagged rows exempt.

`durable` ships as a **tag**, acceptable *because this path writes nothing*: a mistagged row is
down-ranked, not destroyed. **Stated promotion trigger:** the moment durability gates anything
*destructive* — a retirement job, a status-writing demotion — it becomes a validated column. Not
"if it earns it."

`Personal` is deliberately **not** exempted-by-domain: it holds the fastest-moving content in the
store (the job-search pipeline), so a wholesale exemption would let the one thing guaranteed to
age never age. It gets its own treatment in P-Personal, with data.

**Tables touched:** read path only. No writes.

### 7. Contradiction detection surfaces candidates, not verdicts

Same-system, high-similarity current pairs are queued for human confirmation. **Not** automated
contradiction judgment — two similar rows may both be true. At this corpus size, a short review
queue is the win; semantic contradiction judgment is a research problem.

Confirmation writes a transition with `reason_code='contradiction_confirmed'`. Dismissal writes
nothing and does not re-flag.

### 8. Bitemporality — scheduled on a case, not built speculatively

`valid_from` = fact-onset; `created_at` = ingest time. Columns exist. The distinction is
**anticipated, not observed** (data, 2026-07-31): `valid_until` *is* exercised organically
(6 `live:text` retirements against 10 migration artifacts, 16 total), but `valid_from` has never
once *organically* differed from `created_at` — the 743 rows where they differ are OB1→OB2
migration timestamp skew, not backdating.

When built, `valid_from` is set **explicitly at the ingest surface — never an optional `may`**.
Capacity without a caller is how `component_key` reached this state.

`occurred_at` (system time) and `valid_until` (valid time) are **not the same column**: a
backdated retirement sets `valid_until` to fact-offset, possibly earlier than `occurred_at`.

### 9. Not doing — cryptographic signing

The pattern underneath it — transitions as first-class records — is adopted in full. The
cryptography is not. Signing proves *who wrote this and that it was not altered*; the threat
model is single-tenant, single-writer, human-gated, with no untrusted party. A valid signature on
the June note would not have made it less wrong.

Revisit if the vault goes multi-writer, if untrusted agents gain write access, or if we begin
grading agent predictions adversarially.

### 10. Process controls — the three that would have caught rev.4's failure

These are part of the decision, not commentary. The metadata drift was not a design error — the
correct design was reached on 2026-07-31 — it was a document that held two answers and an
implementation step that did not re-check the spec. All three controls are mechanical.

**10a. Two-table parity test, in CI, before P2 resumes.**

A test asserting that `knowledge` and `knowledge_chunked` agree on every mirrored metadata column
for the same `document_id`. It fails the build on any divergence.

This is the test that was never written. Three drift instances occurred in nine days; a
twenty-fixture harness was built for supersession and nothing guarded the failure mode that had
already happened twice. **Write it before the migration 007 work, not after** — it must be able to
go red against the current drifted state, which is the only proof it works. Once item 3 lands the
columns will not exist to diverge, and the test retires with them.

**10b. Restate-before-build.**

Before executing any phase, the implementer re-reads this ADR and states in one sentence what is
about to be built. If that sentence does not match the Decision section, stop. Ten seconds against
two hours of migration rework.

The July 31 design decision was one query away from the implementer and was not retrieved. The
"query OpenBrain at startup" instruction was treated as a session-context checkbox rather than a
per-step reconciliation against the recorded decision that governs that step.

**10c. Reversibility stated per step.**

Every step names its rollback before it runs. If the rollback is not immediate and concrete, the
step does not proceed. This is the property that made yesterday recoverable: P1 wrote nothing, P2
stopped at a safe resting point, and the AMBER dry run caught twelve foundational documents before
they were buried.

Correctness cannot be guaranteed. Recoverability can, and it is the control a non-specialist
reviewer can actually verify.

**10d. Operational validation before any schema change — evidence, not memory.**

"Validate current state" is not done after checking row data and wording. Before authoring OR
applying an `ALTER`/`DROP`, run `scripts/preflight_migration.py <table>` and answer the PM's
operational rubric from its output: column **nullability/defaults/CHECK** state, **real**
index/constraint names (never guessed), the **apply order** (expand/contract — a reader/writer
stops before a column is dropped; a NOT-NULL column gets `DROP NOT NULL` first), **every**
repo reader AND writer of the table (not just the file in the diff), **RLS policies** on the
table, and **derived-table parity**. This control exists because the earlier data/phrasing pass
missed a NOT-NULL that forced an expand/contract split, a fabricated index name, and an
un-migrated second writer — the last one a data-loss-class miss caught only by 10b. A schema
change with no preflight output on the record is not a validated change.

**10e. Trial every prod statement in `BEGIN … ROLLBACK` before it is handed over — the
universal net.** Enumerating object classes ahead of time is fallible (the apply still hit an
RLS-policy dependency at §B and a CHECK violation at §E that preflight/audit had not surfaced).
Running the *exact* statement in a rolled-back transaction reproduces every dependency and
constraint error against real prod data while changing nothing — it does not depend on
remembering what to check. Both apply-time surprises would have been caught pre-handoff by this
one rule. Standing rule: **no prod SQL reaches Mike until its rolled-back trial has passed**, and
the trial result is the evidence, not "I checked it."

---

## Current state — what is live, what is frozen

**Live and verified (P1):** recency net, `OPENBRAIN_RECENCY_HALFLIFE_DAYS=90`, redeployed, smoke
green. Confirmed decaying old unkeyed `Network` rows while exempting keyed and durable rows.
Independent of the P2 freeze.

**Applied, migration 006 §A–D:** `component_key` column on both tables (backfilled from tag);
`system_vocabulary` table + validation trigger on `knowledge`; `CHECK component_requires_system`
added **NOT VALID**; `one_current_per_component` unique index (`NULLS NOT DISTINCT`). Re-key
applied on `knowledge` for 2 rows.

**Deliberately not run:** §E `VALIDATE CONSTRAINT`.

**Why this is a safe resting point:** CHECK (NOT VALID) + trigger + unique index already enforce
new writes; existing `knowledge` rows are clean. The incomplete parts are the un-promoted CHECK
and the `knowledge_chunked` drift — neither breaks retrieval, because reads still use tags.

**Rework required by item 3:** migration 006 §A added `component_key` to `knowledge_chunked`.
That column is now to be dropped, along with the five other mirrored metadata columns. Migration
007 supersedes part of 006 — record it as such rather than editing 006.

**Apply-order defect found at build time (2026-08-01), and why the validation pass missed it.**
Rev.5.1 listed "migration 007: drop the columns" as P2 step 1, ahead of the read-path join. That
order is unsafe: dropping first 500s the live read path and fails the mirror INSERT, and four of
the six columns (`domain/environment/status/tags`) are NOT NULL, so the content-only mirror
cannot even omit them until they are made nullable. The fix is expand/contract — §A `DROP NOT
NULL` before the deploy, §B `DROP COLUMN` after (see Phasing P2, now corrected). **Why the
earlier "reconcile the ADR to tested current state" pass did not catch it:** that pass validated
row-level *data* (the re-key, the drift counts) and *phrasing*, not the schema *constraints*
(column nullability) that govern apply-order safety, and it applied a factual-consistency lens,
not an operational-sequencing one. The nullability was one `information_schema` query away and
was not run until implementation forced it — the same "validate the part you thought to look at,
not the part that bites you" failure this ADR exists to break. Backstop that caught it: item 10b
(restate-before-build). Standing correction: a plan's *apply order* is part of what the
validation pass must check, not just its numbers.

---

## Phasing

- **P0 Measure + standing monitor.** Baseline the same-system high-similarity current-pair
  population. Then two counts **on a schedule through P1/P2**, excluding handoff artifacts:
  (a) current rows with a `component_key` and null `system` — baseline **2**; on `knowledge` now
      **0** after the 2026-08-01 re-key (the matching `knowledge_chunked` rows still carry null
      `system` — instance-#3 drift — cleared when migration 007 drops `system` from chunks);
  (b) `(system, component_key)` with >1 current — today **0**, must stay 0.
- **P1 Recency net — ✅ SHIPPED.** Exit criterion met: harness C3 xpasses at `boost=1.0`, strict
  xfail marker removed.
- **P1.5 Durable first-pass — REQUIRED BEFORE ANY FURTHER DECAY TUNING.** The §4 dry run returned
  **AMBER**: 66 current docs in the allowlisted domains, **0 durable-tagged**, and ~12
  foundational-still-true (OpenBrain architecture, Security guidance, VLAN topology, step-ca CA
  state, migration ADRs, PKI drill). The exemption mechanism exists with **no members** — a decay
  with no floor. Mike marks the durable set; Code produces the candidate list with a reason per
  row (age, domain, component-keyed, first line). **Open question:** should `component_key` imply
  durable? A living doc is by definition the current-state record for its component, so sinking
  one is always wrong — if that holds, keyed rows are exempt structurally and future living docs
  inherit the protection.
- **P2 Metadata ownership + component identity — resume here.** Ordered for **expand/contract**
  safety: reads and the mirror must stop depending on the six columns BEFORE they are dropped,
  and four of them (`domain/environment/status/tags`) are NOT NULL, so the content-only mirror
  cannot omit them until they are made nullable. This **corrects rev.5.1's step order**, which
  listed the column drop as step 1 — dropping first would 500 the live read path and fail the
  mirror INSERT. Caught at build time (item 10b working as intended); see the current-state note
  for why the earlier data/phrasing validation pass did not catch it.
  0. **Parity test (item 10a), confirmed RED.** ✅ merged (#84) — 8 `system`-drift rows.
  1. **Code — build + deploy FIRST.** Retrieval joins to `knowledge` on `document_id` for every
     metadata facet + reads `component_key` from the column (tag fallback); mirror writes
     content-only + drops the tag-based chunk-status UPDATE; `component_key` gets its writer on
     the parent INSERT. ✅ built + live-verified (PR #85). Deploy happens at step 4.
  2. `system` at the ingest API + CLI, validated vs the vocabulary. ✅ already in main (#82).
  3. **Migration 007 §A — `DROP NOT NULL`** on `domain/environment/status/tags`, before the
     deploy. ✅ applied.
  4. **Deploy** the step-1 code (Vercel auto-deploy on #85 merge). ✅ live (READY at 9ab0621).
  5. **Verify** — live smoke exit 0, `capability-audit` PASS, prod spot-check served parent
     `system` for the drifted docs. ✅
  6. **Migration 007 §B — `DROP` the six columns** + indexes. ✅ applied. **Apply-time find:** two
     `anon` RLS policies gated on `status` and blocked the drop — the preflight had not enumerated
     policies (now fixed). App is `postgres`/BYPASSRLS so they governed only the unused REST anon
     surface; dropped them. Parity guard xPASSed → deleted.
  7. **`VALIDATE CONSTRAINT`** (006 §E). ✅ applied. **Apply-time find:** the CHECK is
     unconditional (all statuses), so it caught two `component:adr-018` handoff-artifact rows the
     `current`-scoped audit had excluded; de-registered them (cleared `component_key`) per "ingest
     once, not maintained live", then validated clean. (Trialed in a rolled-back txn first — the
     rule that would have caught both finds pre-handoff; see item 10d.)
  8. **Post-migration `chore(lint)`.** Clear the 163 ruff findings (mostly E501; 50 auto-fixable)
     in a dedicated PR — surgical wraps, not `ruff format`. All Claude-authored; who wrote them is
     irrelevant, they should not exist. ⏳ the one remaining P2 tail.
  - **Precondition — DECIDED:** `system` is a general **namespace** (Mike, 2026-08-01), not
    infra-only; the 2 rows re-keyed to `FlightSim` / `MikeMcMahon-Dev`.
- **P2→P3 window.** ✅ `auto_supersede` now keys supersession on the `component_key` **column**,
  not the `component:*` tag (`api/knowledge_ingest.py`) — the planned P2 step, executed 2026-08-01.
  Proven behaviour-equivalent on live data first (0 rows where the tag value and the column
  disagree among current+system rows). P3 retires the whole mechanism when the projection takes over.
- **P3 Transition records — BUILT + TRIALED (migration staged, 2026-08-01).** Migration 008
  (`supabase/migrations/008_supersession_events.sql`): `supersession_events` append-only/immutable
  table (TEXT+CHECK vocab, not `CREATE TYPE` — the only form that trials under §10e); backfill of
  the **5** live `supersedes_id` chains as `reason_code='migration'` (recon confirmed 5, not 4 — 0
  orphans); projection trigger = sole writer of the `superseded` transition; the guard is the
  `superseding_id → knowledge.id` FK made **DEFERRABLE INITIALLY DEFERRED** (breaks the
  event-first / one_current_per_component ordering knot). Write path switched to event-first in
  BOTH `api/knowledge_ingest.py` (auto_supersede) and `api/ob2_state.py` (confirm) — no direct
  `status='superseded'` UPDATE remains. Reconciliation core (`api/supersession_reconcile.py` +
  `scripts/reconcile_supersession.py`) checks both drift directions, exempts the 67 birth-state
  `historical` rows. Nightly reconciliation wired as a Vercel cron
  (`api/cron_supersession_reconcile.py`, `0 4 * * *`) that appends to migration 008 §D's
  `supersession_reconcile_log` — `drift_count>0` rows are the alert surface (no paging, no
  Vercel→internal-Pushgateway boundary; a Grafana Postgres datasource or email digest reads it
  later). All trialed in `BEGIN..ROLLBACK` (table/backfill 10/10, swap-flow 9/9, confirm 5/5,
  reconcile 5/5, log-write 2/2). **Remaining:** apply migration via Dashboard (expand/contract:
  migration FIRST, then deploy code); CI test on an ephemeral schema (F2/F7/F10 harness).
  Precondition cleared: `environment='Archive'` (9 rows) is orthogonal (all `current`, never
  overlaps historical/superseded).
- **P4 Contradiction detection — BUILT + TRIALED (migration staged, 2026-08-01).** Candidate
  surfacing; the human gate is off the write path. **P0 baseline** (same-system current pairs):
  0 pairs ≥0.92, 2 ≥0.90, **9 ≥0.85** — and the top pairs are same-topic / temporal-sequence /
  append-only session logs, **not** logical contradictions, confirming ADR §7's "two similar rows
  may both be true." So: no auto-judgment; most candidates get dismissed → **dismissal memory is
  the load-bearing requirement**. Migration `009_contradiction_candidates.sql`: pair table
  (canonical `id_lo<id_hi`, `UNIQUE(id_lo,id_hi)`, status pending|confirmed|dismissed) — a
  confirmed/dismissed pair is never re-flagged (`ON CONFLICT DO NOTHING`). `api/contradiction_
  detect.py`: `detect_candidates` (same-system, sim≥threshold, default 0.85 via
  `OPENBRAIN_CONTRADICTION_SIM_THRESHOLD`), `list_pending` (both-current only), `confirm`
  (→`contradiction_confirmed` event on the P3 rails, projection retires the loser), `dismiss`
  (no event). Review CLI `scripts/contradiction_review.py` (detect|list|confirm|dismiss). Nightly
  cron `api/cron_contradiction_detect.py` built + routed but **DORMANT** (not in `vercel.json` —
  ~9 pairs doesn't warrant a scan; one-line enable documented, Mike's call). Trialed 12/12 in
  BEGIN..ROLLBACK (detect→confirm→dismiss→no-reflag→reconcile CLEAN). Scoped same-system only;
  679 null-system floaters are recency-net territory (P1). **Remaining:** apply 009 via Dashboard;
  human-review the 9; enable the cron if volume climbs.
- **P5 Bitemporality — BUILT + TRIALED, PULLED FORWARD (migration staged, 2026-08-01).** Mike's
  call, overriding "defer": in a lab, state churns continuously, so "a change is coming" is a
  standing condition, not a maybe — building ahead of a known caller is the *opposite* of the
  `component_key` capability-without-a-caller trap, and leaving `valid_from` dormant-but-populated
  (it is `NOT NULL DEFAULT now()`, silently claiming fact-onset while delivering ingest-time) is
  itself the landmine. Fix: decouple the two clocks. Migration `010_bitemporal.sql`:
  `supersession_events.fact_valid_until` (nullable valid-time offset; NULL ⇒ projection uses
  `occurred_at`, i.e. P3 behaviour preserved); projection sets `knowledge.valid_until =
  COALESCE(fact_valid_until, occurred_at)`; `CHECK knowledge_valid_span (valid_until IS NULL OR
  valid_from <= valid_until)` (0 existing violations, VALIDATED) — also rejects a backdated offset
  earlier than onset. Callers wired, explicit (not ignored optionals): `valid_from` at the ingest
  surface + `write_knowledge` (also becomes the retired predecessor's fact-offset → contiguous
  lifespans); `fact_valid_until` on all three retire paths (`knowledge_ingest` auto_supersede,
  `ob2_state` confirm, `contradiction_detect` confirm). As-of read: `api/temporal_query.py:as_of`
  + CLI `scripts/as_of.py` — point-in-time state by valid-time (a superseded row still surfaces
  as-of a date inside its real span). Trialed 4/4 (backdated retire, NULL fallback, inverted-span
  reject, validated) + as_of 4/4 in BEGIN..ROLLBACK; `tests/test_bitemporal.py` (skips until 010
  applied). **Remaining:** apply 010 via Dashboard, then merge. `valid_from` now HAS a caller —
  no longer a dormant capability.
- **P6 Fail-loud wiki `is_stale`** — low priority; the wiki is a separate surface
  (`handle_get_wiki` / `handle_compile_wiki`), not the main query path.
- **P-Personal — deferred, unscheduled.** How the fastest-moving domain should age, with real
  data. Numbered so it does not evaporate.

---

## Scope honesty

Rails for the **component-keyed subset** — 6 real current component rows (a 7th is a handoff
artifact, excluded). All **6 now carry a `system`** on `knowledge` and are index-protectable —
the 2 formerly null (`flightsim-hardware`, `mikemcmahon-dev-design`) were re-keyed 2026-08-01. The
*identity* is still only durably guaranteed once P2 plumbs the ingest write-path (`component_key`
has no writer yet), and the chunk rows for those 2 stay null-`system` until migration 007.
Plus a recency net for the ~613 floaters.

This does **not** make the 83% ungrounded corpus temporally grounded. That remains a curation
reality. What it does: makes stale content **sink** (recency), makes designated living docs
**provably single-current** (transitions + index), and makes contradiction **a query a machine
runs** rather than something a solo operator must notice.

**Reconcile `environment='Archive'` before P3.** It is already in use — **9 rows** — so a
retirement-adjacent concept predates this ADR. Establish what those 9 are and whether `Archive`
overlaps `historical` / `superseded`, so we extend rather than duplicate.

**Handoff artifacts are excluded from every count.** The duplicate `component:adr-018` rows came
from writing this ADR *into* OpenBrain to hand it between agents — a workflow artifact, not
organic operation. Counting them would measure our process, not the store. **ADRs are decided in
git and ingested once, not maintained live in the store.**

---

## Consequences

- **Positive.** `status` written in exactly one place. Metadata stored in exactly one place —
  drift becomes structurally impossible rather than monitored. An immutable audit trail of every
  retirement with a reason. Database-enforced single-current-per-component. Contradiction becomes
  runnable. The live stale-ranking bug is already fixed (P1) ahead of any schema change.
- **Cost / risk.** Triggers, a projection, and a reconciliation job are materially more machinery
  than an UPDATE. A faulty projection could diverge silently — mitigated by the reconciliation job
  and the B9 drift test. Item 3 re-touches the read path stabilised on 2026-07-30; the C-suite
  regression tests are the guard against that.
- **Rollback.** Because `supersession_events` is append-only truth, `knowledge.status` is
  rebuildable from it at any time. A faulty projection is recovered by **replay, not restore.**
  Stated so nobody improvises something worse under pressure.
- **Validation.** Every phase measured before and after. C3 (boost-off regression) and C4
  (evergreen negative control) wired into CI. A **read-only dry run against the real corpus with a
  hand-reviewed retire/demote list before anything is applied to production** — non-negotiable.
  A migration that retires 200 rows correctly and 3 wrongly quietly destroys three things you will
  not miss for months.
- **ADR-019 dependency.** The completeness audit is currently **single-table**, inherited from
  rev.2–4 of this document. It cannot police item 3 until it is two-table-aware: the rubric
  (`schema → write → ingest surface → read → enforce → test → docs`) must be evaluated **per
  table**, and a change touching `knowledge` but not `knowledge_chunked` must fail the same way a
  schema-without-a-caller does.
- **ADR-019 wiring.** `make capability-audit` exists as a target with no caller — the audit that
  detects capabilities without callers is itself one. It belongs in `make check` (which already
  assumes a live environment for smoke) with a pre-push hook invoking `make check`. Hard fail if
  the DB is unreachable: OpenBrain is Vercel/Supabase-hosted, so an unreachable DB means a larger
  outage is already in progress.