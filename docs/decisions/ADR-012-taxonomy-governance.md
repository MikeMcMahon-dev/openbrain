# ADR-012: Taxonomy Governance — Controlled Vocabulary, Server-Side Normalization, Approval Queue

**Status:** Proposed
**Date:** 2026-06-17
**Related:** [[ADR-011]] (field integrity at the ingest chokepoint), ADR-007 (knowledge taxonomy),
ADR-008/010 (`component:*` dedup tags). Branch `cut/ob2-cutover`.

## Context

Tags in `public.knowledge` had drifted badly. A live harvest (2026-06-17) found **45 distinct
tags** produced by two coexisting schemes plus casing dups:

- Stage-2 migration emitted rich content/skill tags: `IaC` (367), `RedHat` (127), `Bash` (63),
  `Ansible`, `Terraform`, `Bare-Metal`, `NV-Prep` — genuinely useful searchable facets.
- The OB2 ingest mapper invented a thinner domain-flavored set: `Network`, `Production`, `OpenBrain`.
- Pure drift: `Homelab`/`HomeLab`, `Session`/`Sessions`, `MentalHealth`, and 19 `SmokeTest` rows.

Root cause: **no shared, enforced taxonomy contract.** Every producer (Claude, ChatGPT, each
Custom GPT, each session) invents vocabulary with no awareness of what exists. The fields that
*were* constrained — `domain`/`environment` (validated in `handle_ingest_state` against fixed
enums) — stayed clean; the unconstrained `tags`/`system` drifted. The lesson is direct:
constrain the open fields, and normalize at the single ingest chokepoint rather than trusting
taxonomy-blind producers.

## Decision

**Producers propose; the server disposes.** Govern tags through one vocabulary, normalize at
the chokepoint, and grow the vocabulary only by explicit human approval.

1. **Single canonical vocabulary.** `api/taxonomy_map.py` holds `CANONICAL_TAGS` +
   `TAG_ALIASES` (variant→canonical, with explicit-drop entries like `SmokeTest`→None). Built by
   *unifying* the two drifted schemes — the Stage-2 tech tags are kept, not discarded.

2. **DB `tag_vocabulary` table = runtime source of truth** (`migrations/003_tag_vocabulary.sql`),
   seeded from `CANONICAL_TAGS`. Being a table (not just Python) lets approvals take effect
   **without a code deploy**. The Python set is the seed/bootstrap; the table is authority at
   runtime. `normalize_tags(tags, allowed=…)` accepts the DB vocab as `allowed`.

3. **Server-side normalization at the chokepoint.** `normalize_tags()` folds every tag through
   aliases + the vocabulary, returning `(canonical, unknown)`. Wired into the mapper's `result()`
   and `write_knowledge`, so no producer tag survives unnormalized. Casing/synonym drift is
   structurally impossible.

4. **Closed-by-default, extend-by-intent.** Unknown tags are **never silently dropped or
   accepted**. They go to a `tag_proposals` review queue (`migrations/004_tag_proposals.sql`)
   with occurrence counts + samples; the row still ingests with its known-canonical tags.

5. **Approval mechanism** (`scripts/tag_review.py`): the human console — `--list` ("DB has these
   tags" + pending proposals), `--approve TAG` (adds to `tag_vocabulary`), `--remap RAW=CANONICAL`
   (alias + optional re-tag of affected rows), `--reject`. This is the literal "use an existing
   one, or make a new one" gate. Mutations require `--yes`/`--execute`.

6. **DB backstop trigger** validates `knowledge.tags` against `tag_vocabulary` on write
   (`migrations/003`). **`component:*` carve-out:** ADR-008/010 use free-form `component:<id>`
   tags for the duplicate-`current` dedup — these are *functional*, not descriptive, and are
   exempt from vocabulary validation (allowed by prefix in both the trigger and `normalize_tags`).

7. **Producer schemas** (`docs/CUSTOM_GPT_ACTION_SPEC.yaml`, `CLAUDE_ACTION_SPEC.yaml`, MCP tool
   schemas): `domain`/`environment` become enums; `tags` stays free-form but documented, with a
   note that novel tags are review-queued, not auto-applied. Steers taxonomy-aware producers;
   the server still validates (schemas don't bind a model absolutely).

8. **Drift audit** (`scripts/audit_taxonomy.py`, periodic, read-only): reports non-canonical /
   near-duplicate tags and out-of-enum domain/environment, surfacing drift that slips through and
   when the vocabulary genuinely needs to grow.

## Consequences

- Drift is stopped at the source; the one-time `scripts/retag_knowledge.py` corrects existing rows
  (normalize casings, add `Career`/`Interview`, re-flag mental-health, drop `SmokeTest` rows) and
  must run **before** the `003` trigger is installed (else the trigger rejects existing drift).
- New tags now require a human approval step — intended friction, the whole point of "extend-by-intent".
- **Dual-maintenance** of `CANONICAL_TAGS` (Python seed) vs `tag_vocabulary` (DB authority): the
  DB is the runtime truth; the seed only bootstraps. Approvals write the DB; periodic sync of the
  seed from the table is a housekeeping task, not a runtime dependency.
- The `component:*` carve-out is a deliberate hole in validation — namespaced functional tags are
  trusted by prefix. Audit should still report their cardinality.

## Alternatives considered

- **Leave tags free-form.** Rejected — that is the status quo that produced 45-tag drift.
- **Trust producer-declared enums only (no server normalization).** Rejected — LLMs ignore schemas
  under load; ADR-011 already established producers can't be trusted for field integrity.
- **Silently drop unknown tags.** Rejected — loses signal and the chance to grow the vocabulary;
  the proposal queue preserves intent.
- **LLM auto-classification of every tag at ingest.** Deferred — a tier-2 option for ambiguous
  proposals only, not an always-on cost (consistent with ADR-011's tiered-classifier stance).
