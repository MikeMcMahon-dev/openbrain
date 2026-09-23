-- TEMPLATE.sql — copy this when adding a migration that CREATEs a table in public.
-- Not a migration itself; never applied. Numbered files are NNN_description.sql.
--
-- WHY THE GRANT IS HERE: from 2026-10-30 Supabase no longer auto-grants Data API access
-- to new tables in `public`. A migration that creates a table without grants leaves it
-- unreachable through PostgREST / supabase-js / GraphQL — in new projects, preview
-- branches, and a local `supabase db reset`. The grant belongs in the same migration as
-- the CREATE, or a rebuild of this schema silently loses Data API access to that table.
--
-- WHY ONLY service_role: OpenBrain reaches Postgres through psycopg on a direct
-- connection and does not use the Data API at all. All 16 existing public tables grant
-- everything to service_role and NOTHING to anon or authenticated — verified against the
-- live catalog 2026-09-23. Keep it that way.
--
-- DO NOT grant to anon. Supabase's own migration guidance suggests
-- `grant select ... to anon`, and that advice is WRONG for this project: the anon key is
-- PUBLIC by design (it ships in client bundles), so granting anon SELECT on `knowledge`
-- would publish the knowledge vault to anyone holding it. `scripts/migration_status.py`
-- has a check that fails if anything is ever granted to anon or authenticated.

BEGIN;

CREATE TABLE IF NOT EXISTS public.your_table (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now()
    -- ...
);

-- Required. Without this the table is invisible to the Data API after 2026-10-30.
GRANT SELECT, INSERT, UPDATE, DELETE ON public.your_table TO service_role;

-- If the table has a sequence (bigserial/identity), grant that too:
-- GRANT USAGE, SELECT ON SEQUENCE public.your_table_id_seq TO service_role;

COMMIT;

-- BEFORE HANDING THIS TO MIKE:
--   1. python scripts/preflight_migration.py your_table      (live schema + every reader/writer)
--   2. python scripts/sql_trial.py < supabase/migrations/NNN_your_migration.sql
--      HARD GATE — Mike rejects prod SQL that arrives without the trial output attached.
-- AFTER APPLYING:
--   3. add a CHECKS entry in scripts/migration_status.py AND a row in migration_log.md,
--      in the same change. A file header is not evidence that a migration was applied.
--   4. python scripts/migration_status.py    -> expect all APPLIED, grants checks included
