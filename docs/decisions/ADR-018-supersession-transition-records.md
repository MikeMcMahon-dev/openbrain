# ADR-018: Supersession as append-only transition records

**Status:** Proposed (design; no DB mutation without sign-off + a read-only dry run)
**Date:** 2026-07-31
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
  ×2 boost is the only thing hiding it, and it fires on **3 of 731 current rows**.
- **83% of current rows are temporally ungrounded** — no in-content date, no `component:*`
  tag; `valid_until` set on 15 rows total; supersession-eligible on 3. 91% of the corpus is
  `current` and nothing retires it, so present-tense assertions live forever.
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
2. **`knowledge.status` is a materialised projection** of that truth — kept stored (not
   read-time-derived) so a **partial unique index** `one_current_per_component` can enforce
   *at most one current row per component* at the database level. This is a deliberate
   divergence from the "derive at read time" idea three responders proposed: we take the
   append-only-truth 90% and keep an indexable projection for the uniqueness guarantee.
3. **`component_key` promoted from the `tags` array to a real column** + a vocabulary table,
   so the key supersession pivots on is validated (today an unregistered key is accepted
   silently) and the partial unique index is possible. One fix for two defects.
4. **Enforcement:** a guard mechanism refuses a competing current insert with no accompanying
   transition; a projection mechanism is the *only* writer of `status`; a nightly
   reconciliation job proves stored status matches transitions-derived status (drift = bug).
5. **Recency net** (read-time, `created_at`-based) as the estate-wide safety layer for the
   ~613 floaters that carry no component key and never will.
6. **Contradiction detection** surfaces *candidates* (same-system, high-similarity current
   pairs) for human confirmation — not automated contradiction judgment.
7. **Bitemporality:** ingest may set `valid_from` to fact-onset while `created_at` stays
   ingest time. Columns already exist; the fix is using them.

### Phasing (adopting the plan's order)

- **P0 Measure** — count existing invariant violations (`(system, component)` with >1
  current) and the same-system high-similarity current-pair population. Baseline retrieval.
- **P1 Recency net** — fixes the live bug, zero writes, instantly reversible. **First.**
- **P2 `component_key` column** + vocabulary + partial unique index.
- **P3 Transition records** — `supersession_events`, backfill the 4 `supersedes_id` chains
  as `reason_code='migration'`, switch the write path, guard + projection + reconciliation.
- **P4 Contradiction detection** — candidate surfacing; the human gate moves here off the
  write path.
- **P5 Bitemporality** — explicit `valid_from`. Independent; can move earlier.
- **P6 Fail-loud wiki `is_stale`** — low priority (see Open Questions #7; the wiki is not in
  the main query path).

### Ordering — Code concedes to Chat

Code initially ranked contradiction detection first (value). Chat ranked it fourth
(dependency), and Chat is right: a detected contradiction is resolved by *recording a
supersession*, so without transition records (P3) you'd resolve it with the in-place UPDATE
this ADR removes. P1's recency net already suppresses the measured failure, lowering
detection's urgency; detection's unique value is age-independent conflicts. **Tiebreak is
P0:** if same-system high-similarity current pairs are many, detection is urgent; if a
handful, it follows the foundation.

## Open questions — resolve before the affected phase (Code's concerns)

1. **`knowledge_chunked` is not in the plan, and it is the read path.** Supersession already
   writes *both* tables (`knowledge_ingest.py:97`), retrieval reads *only* `knowledge_chunked`,
   and the two are written in **separate transactions/connections** today — so their statuses
   are not atomically linked. The projection, the unique index, and reconciliation must all
   span both tables (parent status → *every* chunk's status), or the chunked read path will
   serve a status the events table says is superseded. **This is the headline gap and gates P3.**
2. **Guard trigger vs FK ordering.** `supersession_events.superseding_id → knowledge.id` (the
   new row) means the new row must exist before the event can reference it — so the guard
   cannot be a plain `BEFORE INSERT` that requires the event to pre-exist. It likely must be a
   `DEFERRABLE INITIALLY DEFERRED` constraint trigger checked at COMMIT, and the write path
   must wrap knowledge-insert + event-insert in **one** transaction. The write path uses
   explicit `conn.commit()` so multi-statement txns are supported, but the current dual-table
   mirror commits separately — reconcile with #1.
3. **Recency net must not degrade the study/tutor path.** OB's own "temporal awareness is a
   priority, not a filter" finding: current-only reads are catastrophically wrong for Annie's
   study tutor, whose material is old-but-evergreen. A naive age decay buries it. The net must
   be domain-scoped or gentle, and validated against `Study`/`Personal`, not just infra docs.
   Also: **"durable" has no representation today** (no column/tag). Decide tag vs column and
   who marks evergreens — and whether P1 ships with the durable mechanism or a decay mild
   enough not to need it yet.
4. **Fixture embedding determinism.** The harness wants deterministic fixtures, but embeddings
   come from a live API (non-deterministic across versions, costs per run). CI-wired C3/C4
   should use **frozen, precomputed vectors**, not live embedding calls.
5. **Harness isolation.** Ephemeral schema vs dedicated tenant. Migrations apply via the
   Supabase SQL editor (CLI history stale) — automated per-run schema setup is awkward; a
   dedicated-tenant approach puts fixtures in prod tables (risk). Decide before Suite B.
6. **`occurred_at` vs `valid_until`/`valid_from` semantics.** The plan sets
   `valid_until = occurred_at`. Under bitemporality (P5) `valid_until` is *valid-time* (when
   the fact stopped being true), which a backdated correction places before the transition was
   recorded. Pin whether `occurred_at` is system-time or valid-time across P3 and P5.
7. **Wiki-in-path — answered.** The wiki is a separate surface (`handle_get_wiki`/
   `handle_compile_wiki` endpoints), not the main query path. P6 is isolated and low-priority.

## Scope honesty

This builds rails for the **component-keyed subset** (3 rows today, growing) plus a
**recency net** for the ~613 floaters. It does **not** make the 83% ungrounded corpus
"temporally grounded" — that remains a curation reality. What it does: makes stale content
*sink* (recency), makes designated living-docs *provably single-current* (transitions +
index), and makes contradiction a *query a machine runs* rather than a thing a solo operator
must notice.

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
  than an UPDATE; the `knowledge_chunked` consistency (#1) is non-trivial; a bad projection
  could silently diverge (mitigated by the reconciliation job + B9 drift test).
- **Validation:** every phase measured before/after; C3 (boost-off regression) + C4 (evergreen
  negative control) wired into CI; a **read-only dry run against the real corpus with a
  hand-reviewed retire/demote list before anything is applied to production** (test-harness
  Definition-of-Done §5, non-negotiable — a migration that retires 200 right and 3 wrong
  quietly destroys three things you won't miss for months).
