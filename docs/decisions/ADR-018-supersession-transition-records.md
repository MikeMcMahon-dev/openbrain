# ADR-018: Supersession as append-only transition records

**Status:** Proposed (design; no DB mutation without sign-off + a read-only dry run).
Seven open questions resolved with Chat; nine review refinements folded in (see §Resolutions).
**Date:** 2026-07-31 (rev.4 — join-not-cascade, allowlist decay, review refinements 1–10)
**Relates to:** ADR-008 (living-doc identity / component keys), ADR-011 (document model),
ADR-017 (chunking — the read path this must stay consistent with). Supersedes the
`auto_supersede` UPDATE-in-place mechanism in `api/knowledge_ingest.py`.
**Sources:** `chat_handoff/openbrain-supersession-plan.md` + `openbrain-test-harness.md`
(Chat, 2026-07-31), grounded on Code's live-schema verification.

## Context — the measured problem

Temporal correctness was declared fixed in OB2 (status lifecycle + `supersedes_id`), yet
retrieval still serves stale present-tense assertions. Verified against prod (2026-07-31):

- **A June "Technitium is healthy" note outranks the July "Technitium is decommissioned"
  authoritative doc** the moment the component boost isn't present. Query
  `Technitium DNS cluster DANE TLSA certificate trust`: at `boost=1.0` the June-stale doc
  ranks **#0** and the July-authoritative one falls to **#3** (below a superseded row). The
  ×2 boost is the only thing hiding it, and it fires on **3 of 731 current rows** (3 at time
  of measurement; 6 real component-keyed rows as of writing — the count grew as this work
  produced component-tagged docs).
- **83% of current rows are temporally ungrounded** — no in-content date, no `component:*`
  tag; `valid_until` set on 16 rows (15 at measurement); supersession-eligible on 3. 91% of the
  corpus is `current` and nothing retires it, so present-tense assertions live forever.
- **Supersession UPDATEs in place** (`knowledge_ingest.py`: `SET status='superseded'`), and
  **nothing detects that a new record contradicts a current one** — a human has to notice,
  which the cutover post itself says solo operators fail at.
- RRF is **recency-blind**; `created_at` (100% coverage) is unused by ranking.

## Decision

Adopt the transition-record model from the plan, in full, with crypto signing explicitly
excluded. Concretely:

1. **`supersession_events`** — append-only, immutable (no UPDATE/DELETE), first-class:
   `superseded_id`, `superseding_id` (nullable for expiry), `occurred_at`, `reason_code`
   (enum), `reason_note`, `actor`, `method`. This becomes the **truth** of what was retired,
   when, why, and by whom.
2. **`knowledge.status` (parent only) is a materialised projection** of that truth — kept
   stored (not read-time-derived) so a **partial unique index** `one_current_per_component`
   can enforce *at most one current row per component* at the database level. A deliberate
   divergence from full read-time derivation: append-only-truth where it matters, indexable
   projection for the uniqueness guarantee. **`knowledge_chunked` carries NO status** — the
   read path joins chunks to their parent on `document_id` (Q1, resolved with Chat). One
   stored status, no chunk-level cascade to keep in sync, status divergence *structurally
   impossible* rather than merely detected. `created_by` stays denormalised on chunks
   (immutable → no divergence risk; keeps owner-scoping a single-table filter).
3. **`component_key` promoted from the `tags` array to a real column** + a vocabulary table,
   so the key is validated (today an unregistered key is accepted silently). **`system`
   becomes an explicit, required ingest parameter whenever a component key is present** — it
   is settable in `write_knowledge()` but *not exposed by the ingest API/CLI*, so today it is
   inferred, unreliably (same subject → `OpenBrain` for one write, `null` for another), which
   makes a complete `(system, component)` identity impossible to produce on purpose. A
   `CHECK (component_key IS NULL OR system IS NOT NULL)` rejects the incomplete identity at
   write time (the **primary** guard), and the partial unique index uses **`NULLS NOT DISTINCT`**
   (PG17, confirmed) as a secondary belt — because standard NULL-distinctness treats two NULL
   systems as *different*, so `(NULL,'k')` and `(NULL,'k')` **fail to collide** and both rows
   are permitted, which is the gap that lets a null-system component silently duplicate.
4. **Enforcement:** a guard mechanism refuses a competing current insert with no accompanying
   transition; a projection mechanism is the *only* writer of `status`; a nightly
   reconciliation job proves stored status matches transitions-derived status (drift = bug).
5. **Recency net** (read-time, `created_at`-based) as the estate-wide safety layer for the
   ~613 floaters that carry no component key and never will.
6. **Contradiction detection** surfaces *candidates* (same-system, high-similarity current
   pairs) for human confirmation — not automated contradiction judgment.
7. **Bitemporality:** `valid_from` = fact-onset, `created_at` = ingest time — columns exist.
   But the distinction is **anticipated, not observed** (data, 2026-07-31): `valid_until` *is*
   exercised organically (6 `live:text` retirements vs 10 migration artifacts, 16 total), yet
   `valid_from` has never once *organically* differed from `created_at` — the 743 rows where
   they differ are OB1→OB2 migration timestamp skew, not backdating. So P5 is **scheduled for a
   concrete backdating case, not built speculatively**, and when built `valid_from` is set
   **explicitly at the ingest surface — never an optional `may`** (capacity without a caller is
   how `component_key` reached 8 rows half-unusable).

### Phasing (adopting the plan's order)

- **P0 Measure + standing monitor** — baseline the same-system high-similarity current-pair
  population, then run two counts **on a schedule through P1/P2** (excluding handoff artifacts,
  §Corrections): (a) current rows with a `component_key` and null `system` — today **2**, must
  trend to 0; (b) `(system, component_key)` with >1 current — today **0**, must stay 0. A query,
  not a project; it catches a regression during the P1 window when nobody's watching this surface.
- **P1 Recency net** — fixes the live bug, zero writes, instantly reversible. **First.**
  `created_at` decay applied **only to `Network`, `K8s`, `Security`** (the domains where the
  failure was measured — allowlist, not denylist; Q3). `durable`-tagged rows exempt.
  **Done when** the harness C3 xpasses at `boost=1.0` and its strict-xfail marker is removed
  (test-harness defines this exit criterion precisely).
- **P2 `component_key` column** + vocabulary + **explicit-required `system`** (plumbed through
  the ingest API/CLI, validated) + `CHECK (component_key IS NULL OR system IS NOT NULL)` +
  partial unique index `NULLS NOT DISTINCT` on `knowledge` (parent — chunks never carry keys).
  **Sequence:** land the CHECK **first**, *then* hand-re-key the real null-system component rows
  (**2** excluding handoff: `flightsim-hardware`, `mikemcmahon-dev-design`) — so the constraint
  validates each correction as it's made rather than re-keying under the conditions that caused
  the error. **Precondition (Mike's call):** what does the vocabulary *mean* — is `system`
  infrastructure-systems (`SpectreNet`) or general namespaces? Those two rows aren't infra, and
  their re-key sets the convention for every write after. Decide before the backfill, not by
  four hand-edits.
- **P2→P3 window (no silent gap):** `auto_supersede` currently reads the `component:*` **tag**;
  P2 moves that key to a column. P2 **updates `auto_supersede` to read the new column** so live
  supersession stays whole through the window; P3 retires it when the projection takes over.
  (Option (a) of {update / pause / ship-together} — chosen, stated.)
- **P3 Transition records** — `supersession_events`, backfill the 4 `supersedes_id` chains
  as `reason_code='migration'`, switch the write path, the projection writes `knowledge.status`
  only (chunks join, no cascade), guard (deferrable constraint trigger, Q2) + reconciliation.
- **P4 Contradiction detection** — candidate surfacing; the human gate moves here off the
  write path.
- **P5 Bitemporality** — `valid_from` set **explicitly** at ingest (never `may`). **Deferred,
  unscheduled** — the distinction is anticipated, not measured (Decision 7); pull it forward
  only when a concrete backdating case demands it.
- **P6 Fail-loud wiki `is_stale`** — low priority; the wiki is a separate surface, not the
  main query path (verified).
- **P-Personal (deferred, unscheduled)** — decide how the fastest-moving domain (`Personal` /
  the job-search pipeline) should age, with real data. Numbered so it doesn't evaporate.

### Ordering — Code concedes to Chat

Code initially ranked contradiction detection first (value). Chat ranked it fourth
(dependency), and Chat is right: a detected contradiction is resolved by *recording a
supersession*, so without transition records (P3) you'd resolve it with the in-place UPDATE
this ADR removes. P1's recency net already suppresses the measured failure, lowering
detection's urgency; detection's unique value is age-independent conflicts. **Tiebreak is
P0:** if same-system high-similarity current pairs are many, detection is urgent; if a
handful, it follows the foundation.

## Resolutions (Code + Chat, 2026-07-31)

All seven of Code's open questions are resolved. The originals are preserved in git history
(rev. 1 of this ADR); the decisions:

1. **`knowledge_chunked` — join, don't cascade.** Code raised that the chunked read path never
   appeared in the plan and that dual-writing status to both tables is a divergence risk.
   Chat's counter (accepted): **chunks carry no `status` at all** — retrieval joins chunks to
   their parent on `document_id`. Verified against the read path: the three status-composite
   indexes on `knowledge_chunked` exist but **none drive retrieval** (HNSW drives the vector
   path, a seq scan the keyword path); `current` is **82%** of chunks so the filter is barely
   selective (no filtered-HNSW pathology); and `created_by` (the security-critical filter) is
   immutable and stays denormalised, so only the *mutable* field joins. This eliminates the
   status-cascade, the second reconciliation failure mode, and the line-97 dual-UPDATE — status
   divergence becomes *impossible*, not *prevented*. The content dual-write still must be atomic
   (a parent with no chunks is its own bug); status simply isn't part of it.
2. **Guard = `DEFERRABLE INITIALLY DEFERRED` constraint trigger**, checked at COMMIT (the FK
   `superseding_id → knowledge.id` needs the new row to exist first, so a plain `BEFORE INSERT`
   is impossible). Write path wraps knowledge-insert + event-insert in one transaction.
3. **Recency net (Q3).** `durable` ships as a **tag** for P1 — acceptable *because P1 writes
   nothing*: a mistagged row is down-ranked, not destroyed; fix the tag and it returns. **Stated
   promotion trigger:** the moment durability gates anything *destructive* (a retirement job, a
   status-writing demotion) it becomes a **validated column** — not "if it earns it." Decay is
   **opt-in by domain** (`Network`/`K8s`/`Security` only — where the failure was measured), not
   opt-out. This closes Code's exemption gap: `Personal` is **not** evergreen — it holds the
   fastest-moving content in the store (the job-search pipeline record) — so a wholesale
   `Personal` exemption would let the one thing guaranteed to age never age. `Personal` gets its
   own look in **P-Personal (deferred, unscheduled)** — with real data, not a rule inherited
   from an infra bug.
4. **Fixture embeddings:** frozen/precomputed vectors for CI **plus** a separate non-CI job that
   re-embeds fixtures against the live model periodically and flags divergence (Chat's addition —
   otherwise CI stays green while production retrieval drifts).
5. **Harness isolation:** ephemeral schema, not a dedicated prod tenant. Fixtures F2/F7/F10 are
   deliberately-invalid rows; in prod tables one missed teardown turns a fixture contradiction
   into a real answer. Awkward migrations are tooling; polluted production truth is correctness.
6. **`occurred_at` = system/recorded time; `valid_until` = valid-time.** They are *not* the same
   column: a backdated retirement sets `valid_until` to fact-offset (possibly earlier than
   `occurred_at`). Pinned across P3 and P5 so bitemporality doesn't contradict the transition
   record.
7. **Wiki-in-path — answered.** Separate surface (`handle_get_wiki`/`handle_compile_wiki`), not
   the main query path. P6 stays isolated and low-priority.

## Corrections — handoff artifacts are not a real failure rate

The duplicate `component:adr-018` rows came from writing this ADR *into* OpenBrain as a way to
hand the document to Chat — a workflow artifact, not organic operation. The earlier idea of
making that recursion a centrepiece of Context is **withdrawn**: it would misrepresent the
system's real failure rate to a later reader. The technical finding it surfaced stands on its
own without the dupes — `system` isn't settable through the ingest surface, the identity is
half-inferred, and the CHECK is the right fix. **Consequence:** every invariant-violation count
(P0, Scope) **excludes handoff artifacts**, or the baseline measures our process, not the store.
Lesson adopted: ADRs are decided in git and ingested **once**, not maintained live in the store.

## Scope honesty

This builds rails for the **component-keyed subset** — 7 current component rows today, of which
**1 is the `adr-018` handoff artifact** (excluded). Of the **6 real** rows, **4 carry a
`system`** (index-protectable) and **2 do not** — the legit living docs `flightsim-hardware` and
`mikemcmahon-dev-design`, unprotected until P2 plumbs system — plus a **recency net** for the
~613 floaters. It does **not** make the 83% ungrounded corpus "temporally grounded" — that
remains a curation reality. What it does: makes stale content *sink* (recency), makes designated
living-docs *provably single-current* (transitions + index), and makes contradiction a *query a
machine runs* rather than a thing a solo operator must notice.

**Reconcile `environment='Archive'` first:** it is already in use (**9 rows**) — a
retirement-adjacent concept predates this ADR. Before building a parallel mechanism, establish
what those 9 are and whether `Archive` overlaps `historical`/`superseded`, so we extend rather
than duplicate.

## Not doing

**Cryptographic signing / C2PA attestation of transitions.** The pattern underneath it —
transitions as first-class records — is adopted in full; the cryptography is not. Signing
proves *who wrote this and that it wasn't altered*; the threat model here is single-tenant,
single-writer, human-gated, with no untrusted party. A valid signature on the June note
wouldn't have made it less wrong. Revisit if the vault goes multi-writer, if untrusted agents
gain write access, or if we start grading agent predictions adversarially.

## Consequences

- **Positive:** `status` written in exactly one place; an immutable audit trail of every
  retirement with reason; database-enforced single-current-per-component; contradiction
  becomes runnable; the live stale-ranking bug fixed at P1 before any schema change.
- **Cost/risk:** triggers + a projection + a reconciliation job is materially more machinery
  than an UPDATE; a bad projection could silently diverge (mitigated by the reconciliation job
  + B9 drift test). The `knowledge_chunked` risk (#1) is *dissolved*, not just mitigated —
  chunks stop carrying status, so parent and chunks can't disagree about it.
- **Rollback:** because `supersession_events` is append-only truth, `knowledge.status` can be
  rebuilt from it at any time — a faulty projection is recovered by **replay, not restore**.
  State it so nobody improvises something worse under pressure.
- **Validation:** every phase measured before/after; C3 (boost-off regression) + C4 (evergreen
  negative control) wired into CI; a **read-only dry run against the real corpus with a
  hand-reviewed retire/demote list before anything is applied to production** (test-harness
  Definition-of-Done §5, non-negotiable — a migration that retires 200 right and 3 wrong
  quietly destroys three things you won't miss for months).
