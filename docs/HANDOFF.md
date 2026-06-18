# OpenBrain — Session Handoff

**Last updated:** 2026-06-17 (flip-execution session)
**Status:** ✅ **OB2 FLIP EXECUTED — `knowledge` is LIVE in production.** PRs #51–#55 merged.
**Read this before making changes.** Then `docs/OPENBRAIN_NEXT_STEPS.md` for the backlog.

---

## TL;DR — the flip is DONE
OB2 was stalled before cutover (prod ran legacy `public.thoughts`; `public.knowledge` had no
retrieval and was never written by the family path). This is now **complete and live**:
- **Code** (PRs #51–#55): thoughts hotfix, Phase-2 read/write wiring, taxonomy governance,
  mapper-gap fixes, per-owner ingest honor/derive, temporal-aware read, `.vercelignore`.
- **DB migration executed (2026-06-17):** retag (37 updates / 19 deletes) → migrations `003`+`004`
  applied (incl. `tag_vocabulary` RLS policies) → `cutover_migrate --execute` (63 rows) →
  `promote_study_current --execute` (638 study/personal rows → `current`).
- **Flags flipped:** Vercel `OPENBRAIN_READ_TARGET=knowledge` + `OPENBRAIN_WRITE_TARGET=knowledge`.
- **Verified live:** prod serves `knowledge` (`status=current`, facets present); Annie's 31 Linux
  cards `current` under `anneliesepaige`; per-owner current/historical isolation intact.
- **Rollback if ever needed:** set both flags back to `thoughts` + redeploy (instant); `thoughts`
  is untouched, and every migrated row is tagged `source='cutover:%'` for a one-line DELETE.

### Open follow-ups (non-urgent; family is fully live)
1. **Vercel Git auto-deploy** — was broken (GitHub App lacked `openbrain` repo access; now granted).
   Confirm a merge produces a "via GitHub" build; until then deploy with `vercel --prod`.
2. Session-report cron already repointed to `knowledge` (#54). Nightly job reads OB2 now.

<details><summary>Original pre-flip TL;DR (historical)</summary>

OB2 was discovered **stalled before cutover** — production ran legacy `public.thoughts`;
`public.knowledge` was built but had no semantic retrieval and was never written by the family
ingest path. The cutover machinery landed behind gates, dormant until Phase-2 wiring + the flip.
</details>

## How we got here (the arc)
1. Task: ingest Annie's 31-card Linux reference into her isolated vault + validate retrieval.
   Ingest succeeded (31/31). Validation exposed **every query returns 1 result**.
2. Root cause: `retrieve_thoughts` keys RRF fusion on `(file, source)`; all `source_type=text`
   rows collapse to `(None,'text')` → one bucket, and it surfaces the *worst* candidate with
   inflated confidence. Original scaffolding bug, **not** OB2.
3. Discovered OB2 is stalled (knowledge=699 all `historical`, 0 `current`, no query path reads
   embeddings; family ingest writes only `thoughts`).
4. Decision: **finish the OB2 cutover**, with ADR-011 as its retrieval+ingest spec, and add
   taxonomy governance (ADR-012) because tags had drifted across two schemes.

## DONE — built, tested, committed in PR #51
- **Retrieval:** `api/knowledge_retrieval.py` — `retrieve_knowledge`, RRF keyed on `knowledge.id`.
- **Ingest:** `api/knowledge_ingest.py` — `write_knowledge` (field integrity, DB-vocab tag
  normalization, unknown→`tag_proposals` queue, fail-soft).
- **Thoughts hotfix:** `api/_openbrain_api.py` — fusion key now `COALESCE(document_id, id)`;
  carries `id`/`document_id` through `build_row_payload`. **Annie fuzzy 56% → 100%.**
- **Taxonomy governance (ADR-012):**
  - `api/canonical_tags.py` (flat seed, 49 tags) + `api/taxonomy_map.py` (mapper + vocabulary +
    `normalize_tags`; `shape:`/`component:` namespaced tags exempt).
  - `supabase/migrations/003_tag_vocabulary.sql` (vocab table + validation trigger),
    `004_tag_proposals.sql` (approval queue).
  - `scripts/tag_review.py` (`--list/--approve/--remap/--reject`; approve writes DB then
    best-effort appends the seed), `scripts/audit_taxonomy.py` (drift audit).
  - Producer ingest schemas gained `domain`/`environment` enums (both action specs + both MCP).
- **Migration scripts (DRY-RUN only):** `scripts/cutover_migrate.py`, `scripts/retag_knowledge.py`.
- **Docs:** ADR-011 (reframed), ADR-012, `docs/OB2-CUTOVER-PLAN.md`.

## DONE — Phase-2 wiring (branch `feat/ob2-phase2-wiring`, this session 2026-06-17)
- **Read dispatch:** `api/_openbrain_api.py` `retrieve_for_query()` routes `query_payload` +
  `search_payload` → `retrieve_knowledge` when `OPENBRAIN_READ_TARGET=knowledge` (default
  `thoughts`). `_adapt_knowledge_result()` is the tutor-packet adapter — synthesizes
  `source/section` from `domain/system`, passes knowledge facets through additively, so the
  tutor packet + `results` contract are backend-agnostic.
- **Write dispatch:** `_write_text_ingest()` routes → `write_knowledge` when
  `OPENBRAIN_WRITE_TARGET=knowledge` (default `thoughts`), deriving taxonomy from
  (subject, topic) via `map_to_taxonomy`. A mapper `drop` is a silent curation no-op.
- **Family-read status policy (temporal-aware, Mike-confirmed 2026-06-17):** `status` is a
  *priority* signal, not a hard filter — it exists so stale OPERATIONAL state (old homelab
  config) is never served as live. The read path queries `current` first; a high/medium-
  confidence current hit is returned as-is, otherwise it **broadens across all statuses** so
  sparse/young corpora (e.g. Beth's) still surface historical content. Owner (`created_by`)
  scoping isolates each member (verified 0 overlap Annie↔Mike; broaden path verified returning
  Annie's historical rows).
- **Study/personal promotion (`scripts/promote_study_current.py`, gated):** the Stage-2 corpus
  is all `historical`. Study/personal content (`system IS NULL`) is never superseded, so it is
  promoted `historical→current` at flip time (dry-run: 638 rows — Annie 34, Beth 2, Mike 594,
  mmcmahon 8). Operational rows (`system` set: OpenBrain 48 / SpectreNet 12 / PMX-01 1 /
  tenant-a 9, 61 total) stay historical — their live version is governed by supersession /
  wiki compilation, not a blanket promotion.
- **Mapper-gap fixes (`api/taxonomy_map.py`):** `mcp_smoke`→drop; `linux command line`→
  Study/system=Annie/`[Annie,Linux]` (owned by `anneliesepaige` — her Ubuntu summer learning,
  verified already her account); `git / workflow`→Study/`[Reference]`; `homelab notes`→
  Study/`[Homelab,Reference]`; `homelab infrastructure`+`infrastructure`→**topic-split**
  (k8s-migration→K8s/Production, DNS/topology→Network/Production, system=SpectreNet). Added
  `Linux` to the canonical vocab seed + `003` DB seed.
- **Migration-script authority fix:** `scripts/cutover_migrate.py` now `sys.path.insert`s the
  repo root so it imports the governed `api.taxonomy_map` (single authority, ADR-012) instead
  of silently falling back to its stale LOCAL rules — that fallback would have driven a real
  `--execute` with the wrong taxonomy.

## Custom GPT impact (Beth / Annie) — answer: NO action-spec change required
- **Query:** response contract (`results`/`tutor_prompt`/`rules`/`context_used`) is preserved
  by the adapter; the family GPTs keep working unchanged after the flip.
- **Ingest:** family GPTs hit `/openbrain_ingest` (legacy `ingest_payload`). `domain`/
  `environment` are optional in `CUSTOM_GPT_ACTION_SPEC.yaml` (PR #51). **Per-owner policy
  (Mike-confirmed):** owners in `_honor_owners()` (env `OPENBRAIN_TAXONOMY_HONOR_OWNERS`,
  default `mike.mcmahon67`) have explicit `domain`/`environment` **honored** when valid, with a
  **mismatch/typo alert** surfaced in the ingest response `details` (and invalid values fall
  back to derive + alert). Beth (`snapple01`) and Annie (`anneliesepaige`) **derive** from
  subject/topic — their supplied values are ignored. The GPTs need no action-spec change;
  `docs/gpt_instructions/mike_mcmahon67.md` was updated to pass `domain`/`environment` when
  confident and heed the mismatch alert (Beth's/Annie's instruction files unchanged — derive).

## NOT done (next build steps)
1. **The flip** (runtime, human-gated) — see sequence below. Set both target flags to
   `knowledge`. Confirm the all-status family-read policy above first.

## Validation results (all green; DB untouched at 699 rows / 0 cutover-tagged)
- `pytest tests/test_taxonomy_map.py tests/test_knowledge_retrieval.py` → **68 pass**
  (run with `--rootdir=tests` — pytest trips on the `vault/` symlink otherwise).
- `scripts/test_tag_proposal_wiring.py` → 0 failures.
- `cutover_migrate.py` dry-run → **63 migrate (49 current / 14 historical), 2 drop, 0 flag**.
- `retag_knowledge.py` dry-run → **37 updates, 19 SmokeTest deletes**.
- `audit_taxonomy.py` → 0 unknown drift (vocabulary was harvested from real usage).
- `promote_study_current.py` dry-run → **638 promote (Annie 34 / Beth 2 / Mike 594 / mmcmahon 8),
  61 operational left historical**.
- Read path verified end-to-end vs live DB: temporal-priority (current-first, broaden-on-low-
  confidence) returns Annie's historical rows; owner isolation 0 overlap Annie↔Mike.

## Gated execution order (the flip — post-merge, human-gated)
1. ✅ Backup taken → `~/ob_backup_20260617_194224/` (`knowledge.copy`, `thoughts.copy`).
2. `retag_knowledge.py --execute` — **must precede** the `003` trigger.
3. Apply migrations `003` + `004` (Supabase — not auto-applied).
4. `cutover_migrate.py --execute`.
4b. `promote_study_current.py --execute` — promote study/personal (`system IS NULL`)
    historical rows → current (638 rows; operational state stays historical).
5. Phase-2 wiring ✅ (done — `feat/ob2-phase2-wiring`). At flip: set
   `OPENBRAIN_READ_TARGET=knowledge` / `WRITE_TARGET=knowledge` in Vercel env.
6. Post-flip validation (`scripts/smoke_checks.py`, re-run Annie validate against `knowledge`,
   1000-query harness vs the ADR-002 96.9% baseline).

## Open sign-off items (owner: Mike)
- `docs/migrations/003_cutover_migration_report.md` — **excluded from git** (contains sensitive
  row previews); review locally. `004_taxonomy_audit_report.md` is in-repo.
- Confirmed dispositions already encoded: credential-incident row = safe (decommed test box);
  null-subject row = drop; **Annie-assessment → `Study`, `system='Annie'`** (assessment row
  `68558fd7…` tag `Testing`, the two Ubuntu plans tag `Ubuntu Study`); mental-health → re-flag
  `Personal/Archive` tags `[Personal, Mental-health]`; interview content → `career`+`interview`
  tags via `retag`.

## ✅ Pre-`--execute` mapper gaps — RESOLVED (2026-06-17 Phase-2 session)
All gaps below are fixed in `api/taxonomy_map.py` and verified against the real gap rows; the
`003` report is regenerated (0 flag). Dispositions came from Mike this session:
- `Linux Command Line` (Annie's 31 cards) → Study, `system='Annie'`, tags `[Annie, Linux]`.
  Verified these rows are **already owned by `anneliesepaige`** (her Ubuntu summer learning) —
  they migrate under her account, not Mike's. Added `Linux` to the canonical vocab + `003` seed.
- `Homelab Infrastructure` (5) + `Infrastructure` (4) → **topic-split** (Mike's call):
  k8s-migration topics → K8s/Production, DNS/topology → Network/Production, system=SpectreNet.
  Per-row: Homelab Infra = 4 K8s + 1 Net; Infrastructure = 1 K8s + 3 Net.
- `mcp_smoke` → added to `api/taxonomy_map.py` drop-list (was only in the cutover script's set).
- null "Summer break…" row → Mike confirmed **drop** (stays in curation drop-list).
- `Git / Workflow` → Study/`[Reference]`; `Homelab Notes` → Study/`[Homelab, Reference]`.

## Risks / gotchas
- **Merging PR #51 deploys the `thoughts` hotfix to prod** — it changes the shared
  `retrieve_thoughts` (all owners). Validated strictly better/neutral; intended.
- `ingest/` is **gitignored** — diagnostics, the Annie payload, and `backup_public_tables.py`
  live there and are NOT in git (by design; local tooling only).
- **`component:*` carve-out is forward-looking** — no current rows use those functional dedup
  tags; the trigger/`normalize_tags` exempt them for when operational state lands.
- **Single authority:** DB `tag_vocabulary` is runtime truth; `api/canonical_tags.py` is a
  seed/fallback kept current by approve-time best-effort append. A lagging seed is harmless.
- `retag_knowledge.py` **before** `003`'s trigger (else it rejects existing drift).
- Agents' sandboxes block some Bash forms; scripts use `sys.path.insert` and run via
  `.venv/bin/python` directly.

## Key file map (this work)
| Path | Purpose |
|---|---|
| `api/knowledge_retrieval.py` | semantic retrieval over `knowledge` |
| `api/knowledge_ingest.py` | `write_knowledge` + tag normalization/queueing |
| `api/taxonomy_map.py` / `api/canonical_tags.py` | vocabulary + mapper / seed |
| `scripts/tag_review.py` | tag approval console |
| `scripts/audit_taxonomy.py` | nightly drift audit (wire to Vercel cron) |
| `scripts/cutover_migrate.py` / `retag_knowledge.py` | gated migration / cleanup |
| `supabase/migrations/003,004` | vocab table+trigger / proposals queue |
| `docs/decisions/ADR-011, ADR-012`, `docs/OB2-CUTOVER-PLAN.md` | decisions/plan |

## Next 3 actions
1. ✅ PR #51 merged. ✅ Phase-2 wiring built (`feat/ob2-phase2-wiring`) — open its PR & review.
2. Sign off the regenerated `003` migration report (mapper gaps resolved; the by-subject table
   collapses the infra topic-split — per-row writes are 4 K8s+1 Net for Homelab Infrastructure,
   1 K8s+3 Net for Infrastructure — per-row topics confirmed with Mike). Read-policy,
   promotion, and the per-owner ingest honor/derive policy are all Mike-confirmed — no open
   decisions remain before the flip.
3. Execute the gated flip sequence (backup → retag → 003/004 → cutover → set flags → validate).
