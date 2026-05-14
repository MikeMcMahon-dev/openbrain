# OB2 Migration Plan — Stage 2

**Date:** 2026-05-14
**Branch:** `feat/ob2-schema-migration`
**Status:** In progress

---

## Pre-flight checklist

- [x] Supabase backup taken: `backups/pre_ob2_schema_20260514.sql` + `pre_ob2_data_20260514.sql`
- [x] Smoke checks: 33/33 passing on production
- [x] Domain discovery doc reviewed and human-approved: `docs/migrations/001_domain_discovery.md`
- [x] ADRs 007-010 merged to main

---

## Phase 1: Schema migration (additive)

Apply in order via Supabase Dashboard SQL editor:

1. `supabase/migrations/001_knowledge_table.sql` — `public.knowledge` table, trigger, RLS
2. `supabase/migrations/002_wiki_pages.sql` — `public.wiki_pages` table, RLS

**Verify with `python3 scripts/test_migration.py`** — all 5 tests must pass before Phase 2.

---

## Phase 2: Data migration (dry-run → human approval → execute)

```bash
# Dry-run first — inspect output and report
python3 scripts/migrate_thoughts.py

# Review docs/migrations/001_migration_report.md
# If classification looks correct, get human approval, then:
python3 scripts/migrate_thoughts.py --execute
```

**Do NOT run `--execute` without human sign-off on the dry-run report.**

The `--execute` flag prompts for interactive confirmation. All migrated rows
receive `status='historical'`. No rows are auto-promoted to `current`.

---

## Phase 3: Verification

After `--execute`:
- `COUNT(knowledge)` must equal `COUNT(thoughts)` — the script checks this automatically
- Review `docs/migrations/001_migration_report.md` for domain/environment distribution
- Spot-check 3-5 rows per domain bucket in the Supabase dashboard

---

## Rollback

If anything goes wrong before `--execute`: no rollback needed (schema is additive, no data written).

If `--execute` has run and migration needs to be reversed:
```sql
-- Wipe migration rows only (preserves any manually-inserted rows)
DELETE FROM public.knowledge WHERE source LIKE 'migration:thoughts:%';
```

The `public.thoughts` table is untouched throughout — it remains the live data source
until Stage 3 API work is complete and human sign-off is given.

---

## What is NOT done in Stage 2

- `public.thoughts` is not modified, dropped, or renamed
- No API endpoints changed
- No Vercel deployment
- No wiki pages created
- `--execute` is not run by Claude Code — human must approve first
