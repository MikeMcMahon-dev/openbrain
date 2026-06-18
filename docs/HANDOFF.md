# OpenBrain — Session Handoff

**Last updated:** 2026-06-17
**Branch:** `cut/ob2-cutover` · **PR:** [#51](https://github.com/MikeMcMahon-dev/openbrain/pull/51) (open, staged, pre-flip)
**Read this before making changes.** Then `docs/OPENBRAIN_NEXT_STEPS.md` for the backlog.

---

## TL;DR
OB2 was discovered **stalled before cutover** — production still runs on legacy
`public.thoughts`; `public.knowledge` was built but has **no semantic retrieval** and is
never written by the family ingest path. This branch lands the machinery to finish the
cutover **behind gates**, plus a **taxonomy-governance** layer. **Nothing has been executed
against the DB and nothing is flipped.** Merging PR #51 deploys only the live-`thoughts`
retrieval hotfix (an improvement for everyone); the new modules stay dormant until Phase-2
wiring + the flip.

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

## NOT done (next build steps)
1. **Phase-2 wiring** — route `query_payload` → `retrieve_knowledge` and `ingest_payload` →
   `write_knowledge` behind env flags `OPENBRAIN_READ_TARGET` / `OPENBRAIN_WRITE_TARGET`
   (default `thoughts`). Plus a tutor-packet adapter (knowledge results lack `source/file/
   section/heading`). This is the only remaining *build* before a flip is possible.
2. **The flip** (runtime, human-gated) — see sequence below.

## Validation results (all green; DB untouched at 699 rows / 0 cutover-tagged)
- `pytest tests/test_taxonomy_map.py tests/test_knowledge_retrieval.py` → **68 pass**
  (run with `--rootdir=tests` — pytest trips on the `vault/` symlink otherwise).
- `scripts/test_tag_proposal_wiring.py` → 0 failures.
- `cutover_migrate.py` dry-run → **63 migrate (49 current / 14 historical), 2 drop, 0 flag**.
- `retag_knowledge.py` dry-run → **37 updates, 19 SmokeTest deletes**.
- `audit_taxonomy.py` → 0 unknown drift (vocabulary was harvested from real usage).

## Gated execution order (the flip — post-merge, human-gated)
1. ✅ Backup taken → `~/ob_backup_20260617_194224/` (`knowledge.copy`, `thoughts.copy`).
2. `retag_knowledge.py --execute` — **must precede** the `003` trigger.
3. Apply migrations `003` + `004` (Supabase — not auto-applied).
4. `cutover_migrate.py --execute`.
5. Phase-2 wiring + set `OPENBRAIN_READ_TARGET=knowledge` / `WRITE_TARGET=knowledge`.
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
1. Review/merge PR #51 (deploys the `thoughts` hotfix; rest stays dormant).
2. Build **Phase-2 wiring** (flags default `thoughts`; A/B-able) — the last pre-flip step.
3. Execute the gated flip sequence after signing off the migration report.
