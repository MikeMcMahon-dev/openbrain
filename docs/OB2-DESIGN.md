# OpenBrain 2.0 — Design Specification & Claude Code Work Plan

**Status:** Draft — approved for overnight Claude Code execution  
**Author:** Claude Sonnet 4.6 (chat session, Mike McMahon)  
**Date:** 2026-05-13  
**Target repo:** `/Users/Shared/home-lab/open-brain`  
**Portfolio blog:** `/Users/Shared/portfolio-blog`  
**Portfolio site:** https://mikemcmahon.dev  
**OpenBrain production:** https://openbrain-rouge.vercel.app

---

## Context for Claude Code instances

This document is the primary input for a multi-stage overnight Claude Code effort. Read
`CLAUDE.md` first — it governs all git, credential, and testing constraints. This document
governs the *what* and *why* of OpenBrain 2.0. CLAUDE.md governs the *how*.

Non-negotiable constraints inherited from CLAUDE.md:
- `--no-verify` is banned. If a hook blocks a commit, fix the root cause.
- No credentials in any file. Placeholders only. `credential_scan.py` runs via PreToolUse hook.
- All changes: feature branch → PR → merge. Never commit directly to main.
- Smoke tests (`scripts/smoke_checks.py`) must pass before any PR is opened.
- Run `pre-commit run --all-files` before pushing.
- Use `python3` — NOT `python` — for all Python invocations. `python` fails on this system.

---

## Problem Statement

### Root cause (identified in design session, 2026-05-13)

OpenBrain's `public.thoughts` table treats all knowledge as equal atoms in a vector space.
Retrieval is by semantic similarity. This works for stable reference material (Terraform docs,
K8s concepts) but fails catastrophically for operational state that changes frequently.

**The specific failure modes observed:**

1. **No temporal awareness.** A record from May 5 and a record from May 8 about the same
   system component compete equally in retrieval. The May 5 record is actively misleading
   if the state changed.

2. **No supersession.** When Pi-Hole moved from 192.168.100.30 to 192.168.110.30, both
   records exist in the database with equal retrieval weight. There is no mechanism to
   mark the old record as superseded.

3. **No domain separation.** Study notes (NV interview prep, Terraform reference) bleed
   into operational state queries. The retrieval system cannot distinguish "what is the
   current state of SpectreNet" from "what do I know about networking in general."

4. **No state lifecycle.** Knowledge has no status. There is no concept of "current,"
   "superseded," or "historical." Everything is equally present tense.

5. **No compiled synthesis layer.** Every session re-derives understanding from raw chunks.
   Nothing compounds. The Karpathy insight — that a wiki is a persistent, compounding
   artifact — is absent entirely.

### Design reference

This design is informed by two sources:
- **Karpathy's LLM Wiki** (https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f):
  write-time compilation, persistent wiki as compounding artifact, three-layer architecture
- **Nate Jones' Hybrid Blueprint** (Jones 2026 promptkit): database as source of truth,
  wiki as generated artifact, source-of-truth rule, failure mode prevention

---

## Architecture Decision

### The unified knowledge table

Rather than adding specialized sub-tables (one per domain), OpenBrain 2.0 uses a single
unified `knowledge` table that replaces `public.thoughts`. All knowledge types flow through
the same schema, differentiated by taxonomy columns — not by table name.

**Rationale:** Table proliferation ("new topic = new table") creates a system where tools
must know which table to query, breaking as the knowledge graph grows. A single table with
rich metadata columns is queryable from one interface and extensible without schema changes.

**Backward compatibility:** The existing `thoughts` table is NOT dropped. It is preserved
read-only during migration validation, then deprecated. Existing GPT tools continue to work
against `thoughts` during the migration window. New tools target `knowledge`. After
validation and human sign-off, `thoughts` is renamed to `thoughts_archive`.

### The wiki layer

A second table, `wiki_pages`, stores compiled synthesis pages. These are generated
artifacts — never edited directly. If a wiki page is wrong, the source `knowledge` rows
are corrected and the wiki page is recompiled. This is Karpathy's source-of-truth rule.

**Compilation trigger decision:** Wiki pages are compiled ONLY on explicit API call — never
automatically on ingest. Auto-compile on ingest has the following negative consequences:
- Latency: every ingest becomes slow (write + LLM call)
- Cost: N ingests = N LLM compilation calls
- Thrashing: wiki compiled after ingest #3 is stale by ingest #4
- Partial state: compiled wiki reflects incomplete picture mid-burst
- Error coupling: compilation failure would block ingest

Instead: ingest marks affected wiki pages as `is_stale = true`. Human or scheduled job
triggers `POST /api/compile_wiki` explicitly when ready for a coherent snapshot.

### Write safety

Agents get INSERT-only permissions via Supabase RLS. No UPDATE, no DELETE — ever.
To supersede a record, a two-step process is required:
1. Agent: `POST /api/propose_supersession` → creates pending supersession, returns proposal_id
2. Human: `POST /api/confirm_supersession` with proposal_id → commits supersession atomically

A PostgreSQL trigger prevents duplicate Current records for the same system/component
without an explicit supersession chain.

---

## Schema Design

### Table: `public.knowledge`

```sql
CREATE TABLE public.knowledge (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content         TEXT NOT NULL,
  embedding       vector(1536),              -- unchanged from thoughts

  -- Multi-dimensional taxonomy (replaces subject/topic)
  domain          TEXT NOT NULL,             -- Network | K8s | Security | Study | OpenBrain | Personal
  environment     TEXT NOT NULL DEFAULT 'Study',  -- Production | Lab | Study | Archive
  system          TEXT,                      -- SpectreNet | PMX-01 | StanzaLab | null for study content
  tags            TEXT[] NOT NULL DEFAULT '{}',   -- ['Switch','VLAN','Current'] etc — GIN indexed

  -- Temporal lifecycle (the missing layer)
  status          TEXT NOT NULL DEFAULT 'current'
                  CHECK (status IN ('current','superseded','historical','draft')),
  valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_until     TIMESTAMPTZ,               -- NULL means still current
  supersedes_id   UUID REFERENCES public.knowledge(id),

  -- Provenance
  ingest_id       TEXT,                      -- deterministic idempotency key (carried from thoughts)
  source          TEXT,                      -- which agent/session/tool created this
  created_by      TEXT NOT NULL DEFAULT 'mmcmahon',  -- owner (matches token owner map)
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes
CREATE INDEX knowledge_embedding_idx ON public.knowledge
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX knowledge_status_idx ON public.knowledge (status, domain, environment);
CREATE INDEX knowledge_system_idx ON public.knowledge (system, status) WHERE system IS NOT NULL;
CREATE INDEX knowledge_tags_idx ON public.knowledge USING GIN (tags);
CREATE INDEX knowledge_valid_from_idx ON public.knowledge (valid_from DESC);
CREATE INDEX knowledge_supersedes_idx ON public.knowledge (supersedes_id) WHERE supersedes_id IS NOT NULL;
```

### Table: `public.wiki_pages`

```sql
CREATE TABLE public.wiki_pages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_name       TEXT UNIQUE NOT NULL,
  page_type       TEXT NOT NULL
                  CHECK (page_type IN ('system-state','pending-tasks','topology','summary','event-log')),
  content         TEXT NOT NULL,             -- compiled markdown — never manually edited
  compiled_from   UUID[] NOT NULL DEFAULT '{}',
  domain          TEXT NOT NULL,
  system          TEXT,
  generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_stale        BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX wiki_pages_system_idx ON public.wiki_pages (system, page_type) WHERE system IS NOT NULL;
CREATE INDEX wiki_pages_stale_idx ON public.wiki_pages (is_stale, generated_at);
```

### Trigger: prevent duplicate Current records

```sql
CREATE OR REPLACE FUNCTION public.validate_knowledge_insert()
RETURNS TRIGGER AS $$
BEGIN
  IF NEW.status = 'current' AND NEW.system IS NOT NULL THEN
    IF EXISTS (
      SELECT 1 FROM public.knowledge
      WHERE system = NEW.system
        AND status = 'current'
        AND NEW.supersedes_id IS NULL
        AND tags && NEW.tags
    ) THEN
      RAISE EXCEPTION
        'Cannot create duplicate Current record for system=% tags=% without supersession chain. '
        'Set supersedes_id to the ID of the record being superseded.',
        NEW.system, NEW.tags;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER knowledge_insert_validation
  BEFORE INSERT ON public.knowledge
  FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_insert();
```

### RLS Policies

```sql
ALTER TABLE public.knowledge ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.wiki_pages ENABLE ROW LEVEL SECURITY;

CREATE POLICY knowledge_service_all ON public.knowledge
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY knowledge_anon_read ON public.knowledge
  FOR SELECT TO anon USING (status = 'current');

CREATE POLICY wiki_pages_read ON public.wiki_pages
  FOR SELECT TO anon USING (true);

CREATE POLICY wiki_pages_service ON public.wiki_pages
  FOR ALL TO service_role USING (true) WITH CHECK (true);
```

---

## Migration Strategy

### Pre-migration: Supabase backup

No local Postgres instance exists. Before any schema changes, create a backup:

```bash
# Install Supabase CLI if not present
npm install -g supabase

# Login and link project
supabase login
supabase link --project-ref <PROJECT_REF>

# Export full schema + data
supabase db dump --schema public > backups/pre_ob2_schema_$(date +%Y%m%d).sql
supabase db dump --schema public --data-only > backups/pre_ob2_data_$(date +%Y%m%d).sql
```

Supabase Pro plan also provides automated daily backups via Dashboard → Settings → Backups.
Verify these are enabled before proceeding.

**STOP if backup cannot be verified. Do not proceed without a confirmed backup.**

### Phase 1: Domain discovery (run BEFORE writing migration script)

Stage 1 MUST run these SQL queries against the production Supabase database and save output.
This is not optional — it prevents domain cross-contamination in the migration mapping.

```sql
-- 1. Discover all unique subject/topic combinations
SELECT
  subject,
  topic,
  COUNT(*) as row_count,
  MIN(created_at) as earliest,
  MAX(created_at) as latest
FROM public.thoughts
WHERE subject IS NOT NULL OR topic IS NOT NULL
GROUP BY subject, topic
ORDER BY row_count DESC;

-- 2. Count rows with no taxonomy
SELECT COUNT(*) as null_taxonomy_count
FROM public.thoughts
WHERE subject IS NULL AND topic IS NULL;

-- 3. Sample content per subject (verify what each domain actually contains)
SELECT DISTINCT ON (subject)
  subject, topic, LEFT(content, 300) as content_preview, created_at
FROM public.thoughts
ORDER BY subject, created_at DESC;
```

Save complete output to `docs/migrations/001_domain_discovery.md`.
Do NOT hard-code domain mappings without this output. Unknown subjects default to
`domain='Study', environment='Study'` — never guess at content classification.

### Phase 2: Schema migration (additive only)

Apply `supabase/migrations/001_knowledge_table.sql` and `002_wiki_pages.sql`.
Do NOT touch `thoughts` table.

**Required SQL tests (all must pass before Phase 3):**

```sql
-- Test 1: Table exists and is empty
SELECT COUNT(*) FROM public.knowledge;  -- expect 0

-- Test 2: Valid insert succeeds
INSERT INTO public.knowledge (content, domain, environment, system, tags, status)
VALUES ('test', 'Network', 'Production', 'SpectreNet', ARRAY['Switch'], 'current')
RETURNING id;
-- Store returned id as TEST_ID

-- Test 3: Duplicate current without supersession raises exception
INSERT INTO public.knowledge (content, domain, environment, system, tags, status)
VALUES ('test2', 'Network', 'Production', 'SpectreNet', ARRAY['Switch'], 'current');
-- Expected: ERROR: Cannot create duplicate Current record...

-- Test 4: Supersession chain is allowed
INSERT INTO public.knowledge (content, domain, environment, system, tags, status, supersedes_id)
VALUES ('updated', 'Network', 'Production', 'SpectreNet', ARRAY['Switch'], 'current', '<TEST_ID>');
-- Expected: success

-- Test 5: Anon role sees only current records
SET ROLE anon;
SELECT COUNT(*) FROM public.knowledge;
RESET ROLE;
-- Count should equal only 'current' rows
```

### Phase 3: Data migration

Script: `scripts/migrate_thoughts.py`

```python
#!/usr/bin/env python3
"""
OpenBrain 2.0: public.thoughts -> public.knowledge
Default: dry-run (prints, does not write).
Requires --execute flag to write. Requires human approval before --execute.

IMPORTANT: Load domain mapping from docs/migrations/001_domain_discovery.md first.
Do NOT hard-code mappings. Unknown subjects -> domain='Study', environment='Study'.
"""
import argparse

def load_domain_mapping() -> dict:
    """Raises FileNotFoundError if 001_domain_discovery.md missing."""
    ...

def classify_row(subject: str | None, topic: str | None, mapping: dict) -> dict:
    """Returns {domain, environment, system, tags}. Defaults to Study if unmapped."""
    ...

def migrate(dry_run: bool = True):
    mapping = load_domain_mapping()
    # Read all thoughts rows with embeddings
    # Classify each row using mapping
    # Set status='historical' for ALL migrated rows (humans promote to current manually)
    # Write to knowledge preserving: ingest_id, created_by, created_at, embedding
    # Verify: COUNT(knowledge) == COUNT(thoughts)
    # Write docs/migrations/001_migration_report.md
    ...

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true',
                        help='Write to DB (default: dry-run only)')
    args = parser.parse_args()
    migrate(dry_run=not args.execute)
```

**Critical:** All migrated records get `status='historical'`. No auto-promotion to current.
The `--execute` flag requires explicit human confirmation before running in production.

---

## Stage Breakdown for Claude Code

### Stage 1: ADR + Domain Discovery (60-90 min)

**Read first:**
- `CLAUDE.md`
- `docs/decisions/` (format reference)
- `docs/OPENBRAIN_NEXT_STEPS.md`
- This file

**Run first — domain discovery SQL (required):**
Connect to Supabase (credentials in `.env.local` or Vercel env vars).
Run all three domain discovery queries above.
Save full output to `docs/migrations/001_domain_discovery.md`.

**Create:**
```
docs/decisions/004_knowledge_table.md
docs/decisions/005_temporal_lifecycle.md
docs/decisions/006_wiki_layer.md
docs/decisions/007_write_safety.md
docs/migrations/001_domain_discovery.md    ← SQL query output
docs/OPENBRAIN_NEXT_STEPS.md              ← UPDATE
```

**Branch:** `docs/ob2-architecture-decisions`

---

### Stage 2: Database Migration (90-120 min)

**Read first:**
- `CLAUDE.md`
- ADRs from Stage 1 (docs/decisions/004-007)
- `docs/migrations/001_domain_discovery.md` — REQUIRED, do not skip

**Pre-flight:**
```bash
python3 scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app
# All tests must pass — STOP if any fail
```

**Create:**
```
supabase/migrations/001_knowledge_table.sql
supabase/migrations/002_wiki_pages.sql
scripts/migrate_thoughts.py
scripts/test_migration.py
docs/migrations/001_migration_plan.md
backups/                                    ← directory, .gitignored
```

**Test sequence:** Run all 5 SQL tests from Phase 2. All must pass.
Run `python3 scripts/test_migration.py`. All must pass.
Run `python3 scripts/migrate_thoughts.py` (dry-run). Review report.

**Do NOT run `--execute`.** Human must approve first.

**Branch:** `feat/ob2-schema-migration`

---

### Stage 3: API Updates (120-150 min)

**Read first:**
- `CLAUDE.md`
- `api/_openbrain_api.py`
- `api/app.py`
- `scripts/smoke_checks.py`
- ADRs from Stage 1

**Modify:**
```
api/_openbrain_api.py      # add knowledge table support, optional temporal params
api/app.py                 # register new routes
scripts/smoke_checks.py    # add 10+ new test cases
```

**Create:**
```
api/ob2_state.py           # ingest_state, propose/confirm supersession, query_state
api/ob2_wiki.py            # compile_wiki, query_wiki
```

**New endpoints:**

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/ingest_state` | Insert knowledge with temporal fields |
| POST | `/api/propose_supersession` | Create draft supersession (no commit) |
| POST | `/api/confirm_supersession` | Atomically commit supersession |
| GET | `/api/query_state` | Query current knowledge temporally ordered |
| POST | `/api/compile_wiki` | Explicitly compile a wiki page |
| GET | `/api/wiki/{page_name}` | Return compiled wiki page |

**All `/openbrain_*` and `/claude_*` endpoints must continue working unchanged.**

**After writing:**
```bash
ruff check api/
python3 scripts/smoke_checks.py --live https://<preview-url>.vercel.app
# Target: all 36+ cases pass
```

**Branch:** `feat/ob2-api-endpoints`

---

### Stage 4: Portfolio Blog Documentation (45-60 min)

**Read first:**
- `/Users/Shared/portfolio-blog/CURRENT_STATE.md`
- `/Users/Shared/portfolio-blog/SESSION_HANDOFF.md`
- 2-3 existing MDX posts in `src/content/blog/` (for frontmatter format)
- ADRs from Stage 1

**Blog post:** `/Users/Shared/portfolio-blog/src/content/blog/project-openbrain-2-architecture.mdx`

Category: `projects` (not `sessions`). Match frontmatter exactly from existing project posts.

Site auto-deploys on merge to main (Vercel). GitHub: `Spectre-63/portfolio-blog`.

**Blog post content:**
1. What OpenBrain is (brief — audience has context)
2. The problem: temporal blindness — Tyson's theorem moment
3. Specific failure modes (Pi-Hole IP change, stale state, study content bleeding into ops)
4. Design influences: Karpathy LLM Wiki + Jones Hybrid Blueprint (link both)
5. The unified knowledge table with temporal lifecycle
6. Write safety: append-only + two-step supersession
7. Wiki compilation: explicit trigger only, not auto-compile
8. What's next

**Also create in OpenBrain repo:**
```
README.md                ← UPDATE: add OB2 status section
docs/OB2-USER-GUIDE.md  ← CREATE: session startup protocol for Claude
```

**Branch:** `docs/ob2-portfolio-documentation`

---

## Session Startup Protocol (Post OB2)

At conversation start, Claude queries:

```
GET /api/wiki/spectrenet-current-state   → compiled current network state
GET /api/wiki/pmx01-network              → Proxmox current state
GET /api/wiki/pending-tasks              → open items

# If any page is stale or absent:
GET /api/query_state?domain=Network&status=current&limit=20
GET /api/query_state?domain=Network&environment=Production&limit=10
# Both return ordered by valid_from DESC
```

---

## Resolved Design Questions

1. **No local Postgres:** Use `supabase db dump` for backup. Test SQL via Supabase Dashboard
   SQL editor or Supabase preview project before applying to production.

2. **Domain mapping:** Must be derived from domain discovery query in Stage 1.
   Hard-coded assumptions prohibited. Unknown → Study/Study default.

3. **Compile on ingest:** Rejected. Explicit trigger only. Ingest marks wiki stale;
   compilation is a separate step.

4. **Blog content path:** `src/content/blog/` as MDX. Category: `projects`.
   Site: https://mikemcmahon.dev. Auto-deploys on merge to main.

---

## What NOT to do (all stages)

- Do not modify `public.thoughts` schema
- Do not drop or rename `public.thoughts` without explicit human approval
- Do not write credentials into any file
- Do not merge to main directly
- Do not use `--no-verify`
- Do not skip smoke tests
- Do not use `python` — use `python3`
- Do not create sub-tables beyond `knowledge` and `wiki_pages`
- Do not modify `api/tutor.py`
- Do not run migration `--execute` without human confirmation
- Do not auto-compile wiki on ingest
