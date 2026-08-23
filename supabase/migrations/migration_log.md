# Migration log — what is actually applied to production

**This file is the answer to "has migration NNN been applied?"** Check it before writing,
applying, or depending on any migration.

## Why this exists

There was no record. Supabase's own `supabase_migrations.schema_migrations` table contains only
four CLI-era entries from March 2026 (`add_slack_username`, `supabase_primary_schema_and_tenancy`,
`add_user_identity_tenancy_fields`, `enable_thoughts_rls`) — **none** of the numbered `001`–`013`
series appears in it. Every numbered migration here has been applied by hand, either through the
Supabase dashboard or a direct `psycopg` connection using `SUPABASE_DB_URL`, and nothing recorded
it. Asking "is 012 applied?" meant inspecting the catalog for its objects.

Worse, the per-file headers drifted: on 2026-08-23 both `007` and `012` still read
`STAGED — NOT YET APPLIED` while both had in fact been applied and their objects were live in
production. A header a human must remember to update is a header that lies.

## Rules

1. **Applying a migration means updating this file in the same change.** Not later.
2. **Do not trust a migration file's own header** — trust the Verify query below, which reads the
   live catalog. Headers are prose; the catalog is the fact.
3. Every migration needs a **Verify** query that returns the applied/not-applied answer without
   re-running the migration.
4. `001`–`013` were applied by hand with no transcript. Dates marked *(unrecorded)* are inferred
   from git history or session notes and are **not** independently verifiable — the objects exist,
   but when and by whom is lost. Everything from `014` on must be recorded here at apply time.

## Status

| # | What it does | Applied | When / by | Verify |
|---|---|---|---|---|
| 001 | `knowledge` table | ✅ | *(unrecorded)* | `to_regclass('public.knowledge')` not null |
| 002 | `wiki_pages` table | ✅ | *(unrecorded)* | `to_regclass('public.wiki_pages')` not null |
| 003 | `tag_vocabulary` + validation trigger | ✅ | *(unrecorded)* | `to_regclass('public.tag_vocabulary')` not null |
| 004 | `tag_proposals` approval queue | ✅ | *(unrecorded)* | `to_regclass('public.tag_proposals')` not null |
| 005 | `knowledge_chunked` | ✅ | *(unrecorded)* | `to_regclass('public.knowledge_chunked')` not null |
| 006 | `component_key` + `system` on `knowledge` | ✅ | *(unrecorded)* | both columns present on `public.knowledge` |
| 007 | chunks become content-only; metadata JOINs from parent | ✅ | *(unrecorded)* | **0** of `status,system,tags,domain,environment,component_key` remain on `knowledge_chunked` |
| 008 | `supersession_events` (append-only) + projection trigger | ✅ | 2026-08-01 | `to_regclass` not null **and** trigger `supersession_events_no_mutate` exists |
| 009 | `contradiction_candidates` | ✅ | 2026-08-01 | `to_regclass('public.contradiction_candidates')` not null |
| 010 | bitemporality: `valid_from` / `valid_until` | ✅ | 2026-08-01 | both columns present on `public.knowledge` |
| 011 | drop stale `validate_knowledge_chunked_insert` trigger + function | ✅ | 2026-08-06, direct psycopg | **0** non-internal triggers on `knowledge_chunked`; **0** `pg_proc` rows named `validate_knowledge_chunked_insert` |
| 012 | `retirement_requests` airlock table | ✅ | 2026-08-22/23 *(unrecorded — applied by an agent session, not by Mike)* | `retirement_requests` = 13 cols / 8 constraints / 4 indexes |
| 013 | drop `retirement_requests.target_id` FK | ✅ | 2026-08-23, direct psycopg (trialed BEGIN..ROLLBACK first) | **0** rows in `pg_constraint` with `contype='f'` on `public.retirement_requests` |

## Verify everything in one query

Run this against production. Every row should read `APPLIED`. Anything else is drift between this
file and reality — and reality wins.

```sql
SELECT '001 knowledge'      AS migration, CASE WHEN to_regclass('public.knowledge')                IS NOT NULL THEN 'APPLIED' ELSE 'MISSING' END AS state
UNION ALL SELECT '002 wiki_pages',        CASE WHEN to_regclass('public.wiki_pages')              IS NOT NULL THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '003 tag_vocabulary',    CASE WHEN to_regclass('public.tag_vocabulary')          IS NOT NULL THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '004 tag_proposals',     CASE WHEN to_regclass('public.tag_proposals')           IS NOT NULL THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '005 knowledge_chunked', CASE WHEN to_regclass('public.knowledge_chunked')       IS NOT NULL THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '009 contradiction',     CASE WHEN to_regclass('public.contradiction_candidates') IS NOT NULL THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '012 retirement_requests', CASE WHEN to_regclass('public.retirement_requests')   IS NOT NULL THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '006 component_key+system', CASE WHEN (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='knowledge'
          AND column_name IN ('component_key','system')) = 2 THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '010 bitemporal', CASE WHEN (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='knowledge'
          AND column_name IN ('valid_from','valid_until')) = 2 THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '007 chunks content-only', CASE WHEN (SELECT count(*) FROM information_schema.columns
        WHERE table_schema='public' AND table_name='knowledge_chunked'
          AND column_name IN ('status','system','tags','domain','environment','component_key')) = 0
        THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '008 append-only guard', CASE WHEN EXISTS (SELECT 1 FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid WHERE c.relname='supersession_events'
          AND t.tgname='supersession_events_no_mutate') THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '011 stale trigger dropped', CASE WHEN (SELECT count(*) FROM pg_proc
        WHERE proname='validate_knowledge_chunked_insert') = 0 THEN 'APPLIED' ELSE 'MISSING' END
UNION ALL SELECT '013 retirement FK dropped', CASE WHEN (SELECT count(*) FROM pg_constraint
        WHERE conrelid='public.retirement_requests'::regclass AND contype='f') = 0
        THEN 'APPLIED' ELSE 'MISSING' END
ORDER BY 1;
```

Convenience wrapper: `python scripts/migration_status.py`
