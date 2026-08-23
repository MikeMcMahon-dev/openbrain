# OpenBrain — Session Handoff

**Last updated:** 2026-08-23 (late session, ended for a macOS update)
**Status:** ✅ Prod healthy. CI/CD exists for the first time. Plan/apply shipped but **dark**.
**Read this first.** Then `docs/OPENBRAIN_NEXT_STEPS.md` for the backlog.

---

## ⏩ WHERE WE ARE NOW (2026-08-23) — start here

### The through-line of this session

One bug kept reappearing in different costumes: **a capability existed in the backend that no
surface exposed, so nobody could use it and nobody could tell.** First `system`/`component` (the
supersession keys), then the ingest plan, then retirement. Every fix in this session is a variant
of *close the gap between what the backend can do and what a caller can reach.*

### Shipped and LIVE in prod

| PR | What | State |
|---|---|---|
| #106 | plan/apply handshake (`plan_ingest` + token) | live, **enforcement OFF** |
| #107 | Decimal serialization fix + wire-contract tests | live |
| #108 | plan surfaces: one REST route for stdio + both Action specs, `ob_ingest --plan` | live |
| #109 | Tier 2 preview smoke (restored after a merge race) | live |
| #105 | retirement airlock (code) | live; **migration 012 APPLIED 2026-08-23** |

### Three-tier validation gate (new — none of this existed yesterday)

There was **no CI at all** before this session. `make smoke` and pytest existed and worked; nothing
ran them. "We run the smoke tests" meant "we remember to," which is how a serialization bug reached
production through a PR whose unit tests were all green.

1. **`.github/workflows/ci.yml`** — lint + pytest + `smoke --read-only`, on every PR.
2. **`.github/workflows/preview-smoke.yml`** — hits the real Vercel preview URL over HTTPS after
   deployment, via a Protection Bypass for Automation secret.
3. **`make ci`** — reproduces the gate locally in the same order.

**CI caught three real bugs in its first three runs**, two of them mine (a missing `httpx` dep that
a dev venv was masking, and a `secrets`-in-step-`if` that fails a workflow at parse time with zero
jobs and no log).

**SAFETY — do not remove `--read-only` from CI.** Local smoke WRITES: the PDF and DOCX/URL groups
POST real fixtures to `/api/ingest`. There is one live vault and no staging DB. That flag is the
only thing stopping a pull request from injecting fixture rows into production.

### Prod data changes applied this session

- **FlightSim consolidated.** Three-generation lineage, contiguous, no gaps:
  `0ccc7e4a` (3,784) → `b5041d45` (12,750) → **`6899a59a` (18,953, current)**, 26 chunks.
  Two keyless strays (`400a4e85`, `997ce045`) hard-deleted after verification. Zero keyless
  FlightSim rows remain.
- **`durable` tagging** — 18 rows (mental-health series + clean identity/family). Vault-wide
  `durable` membership is now 26.
- **Food/health cleanup** — 10 dead food-log rows deleted; 3 more retired to `historical`
  (two Weight Loss Project Logs + a now-false "maintain a daily food log" standing directive that
  would have made an agent resume logging).
- **Migration 012 applied** — `retirement_requests`, 13 columns / 8 constraints / 4 indexes,
  0 rows. `scripts/retirement_review.py list` works.

---

## 🔜 NEXT THREE ACTIONS

1. **Teach `ob_ingest.py` to auto-plan.** ~10 lines reusing the `--plan` code path: run the plan,
   fold `plan_token` into the ingest payload. **This is a hard prerequisite for #2** — see the trap
   below.
2. **Enable `OPENBRAIN_REQUIRE_INGEST_PLAN=1`** in Vercel (production + preview). Only after #1.
3. **Exercise the retirement airlock end-to-end** — a real propose → deny → propose → approve →
   execute cycle. The table is live but has never been used; the CLI is unproven against real data.

---

## ⚠️ TRAPS — read before touching these

**Enabling plan enforcement today would break `ob_ingest.py`.** The gate fires on
`source_type=text` + honor-owner, which is exactly what `ob_ingest.py` sends, and its normal path
carries no `plan_token` (the only `plan_token` reference is inside the `--plan` preview branch).
Every session wrap would 409 — including the WAF-workaround path we depend on. Unaffected: smoke
(`source_type=obsidian`), the PDF/DOCX evals, family GPTs (not honor-owners), and Chat (it has
`plan_ingest`).

**A `retire` forecloses a later `delete`.** `supersession_events` is append-only (enforced by
`supersession_events_no_mutate`) and its FK to `knowledge` is NOT deferrable, so once an event
references a row, that row is permanently pinned as historical. Deleting stays open either way —
so when both are viable, decide deliberately. The three food-log rows retired this session are now
permanently non-deletable, which was the intended tradeoff.

**MCP clients cache `tools/list` at connect time.** A schema change deploys fine and the client
keeps offering the old fields until the connector is disconnected and reconnected. From the client
side this is indistinguishable from a failed deploy. Documented in `docs/MCP_SETUP.md` with a
verification `curl`; confirm the server side before anyone cycles connectors.

**A merge race is invisible from the branch you are standing on.** PR #107 merged at `870eaed`
while two later commits were still in flight; they never reached main, and the failure looked like
an environment problem for three tool calls. If a check goes green and then behaves differently on
main, run `git merge-base --is-ancestor <sha> main` on your last few commits *before* debugging the
environment. Note cherry-picked commits are correctly "not ancestors" — compare **content**, not
SHAs.

**Preview does not automatically mirror production.** Vercel env vars are per-environment and are
baked in at deploy time. `OPENBRAIN_READ_TARGET` and `OPENBRAIN_COMPONENT_BOOST` are now scoped to
preview as well; `OPENBRAIN_WRITE_TARGET` is deliberately production-only so a preview build can
never write into `public.knowledge`. **Keep it that way.** Changing a var does not update existing
deployments — a redeploy is required.

**Similarity cannot distinguish an update from a note.** Measured across 228 keyless rows: the one
genuine mis-filed update scored 0.810 and ranked *fourth*, below three legitimate session wraps at
0.834/0.823/0.815. Topic similarity measures SUBJECT; update-vs-note is INTENT. Do not build a
detector on it — the plan shows candidates and a human/agent decides.

**The component boost is load-bearing, not a tiebreak.** On the FlightSim query the keyless stray
beat the merged living doc on raw RRF (0.032787 vs 0.031025); the merged doc only won via the ×2
boost. Consolidation trades chunk-level precision for coherence and the boost pays for it. Turning
`OPENBRAIN_COMPONENT_BOOST` off would quietly regress living-doc retrieval.

---

## 📋 OPEN / DEFERRED

- **Four identity rows deliberately left un-`durable`** pending content correction — `7d401b92`,
  `87984bda`, `90112142` (Annie context), `be162393`. All carry stale facts (Las Vegas;
  `be162393` also Technitium and "5-node cluster"). `durable` must mean *permanently true*, not
  *permanently kept* — tagging them would pin wrong data.
- **`725e287e`** — titled "Test save: …" inside the mental-health cluster. Probable test artifact;
  needs Mike's call. Also ~5 near-duplicate Betrayal Wound *Session 1* rows, deliberately untouched.
- **Recency net for `Personal`: NOT RECOMMENDED** on current evidence (P0 measured it —
  `scripts/recency_baseline.py`). The career corpus self-refreshes (median 23d) so decay barely
  demotes it, while identity docs at 154d would take ×0.31. The career-staleness problem is a
  **lifecycle** problem — a filled req should stop being `current` — not a recency one.
- **Career pipeline has no `system`** and therefore cannot participate in supersession. ~33 rows
  per 60 days land unfilable, including a "Job Search — Pipeline Status" doc and an ADR that are
  textbook living-doc candidates.
- **`scripts/` carries ~100 pre-existing `E501`s** ("Lint pass 2"). CI lint is scoped to
  `api/ mcp_server/ tests/` so it is green on arrival; `scripts/` joins once that chore lands.
- **Food-log specs describe an unbuilt feature** — `FOOD_LOG_FORMAT_SPEC.md` +
  `FOOD_LOG_IMPLEMENTATION_GUIDE.md`. Only the tag vocabulary was ever implemented. Same dormant
  shape as the wiki: give them a decomm date or delete them.

---

## 🔑 Access notes

- `VERCEL_AUTOMATION_BYPASS_SECRET` lives in gitignored `.env.local` and as a GitHub **repo**
  secret. It grants read access to every preview build; revocable via the Vercel project API.
- GitHub repo secrets configured: `SUPABASE_DB_URL`, `OPENBRAIN_TOOL_ACCESS_TOKEN`,
  `OPENROUTER_API_KEY`, `VERCEL_AUTOMATION_BYPASS_SECRET`. With them present the suite runs
  `169 passed`; without, `143 passed, 26 skipped` — **the skip count is the tell** that secrets
  are missing.
- Branch `fix/plan-ingest-decimal-serialization` is safe to delete (content-verified identical to
  main on all files its unmerged commits touched).

---

## 📜 PRIOR STATE — 2026-06-18 OB2 flip (historical, kept for the arc)

Everything below predates the 2026-08-23 session above. Retained because the OB2
cutover reasoning is still the best explanation of why `knowledge` serves prod.
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
