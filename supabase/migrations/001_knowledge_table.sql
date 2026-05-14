-- OB2 Migration 001: public.knowledge table
-- Stage 2 — additive only, does NOT touch public.thoughts
-- Apply via Supabase Dashboard SQL editor or: supabase db push
-- Run scripts/test_migration.py to verify before Stage 3

-- ── Table ─────────────────────────────────────────────────────────────────────

CREATE TABLE public.knowledge (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content         TEXT NOT NULL,
  embedding       vector(1536),                    -- same model as thoughts (text-embedding-3-small)

  -- Multi-dimensional taxonomy
  domain          TEXT NOT NULL,                   -- Network | K8s | Security | Study | OpenBrain | Personal
  environment     TEXT NOT NULL DEFAULT 'Study',   -- Production | Lab | Study | Archive
  system          TEXT,                            -- SpectreNet | PMX-01 | OpenBrain | null for study content
  tags            TEXT[] NOT NULL DEFAULT '{}',    -- e.g. ["IaC","RedHat"], ["component:L3208","Switch"]

  -- Temporal lifecycle
  status          TEXT NOT NULL DEFAULT 'current'
                    CHECK (status IN ('current', 'superseded', 'historical', 'draft')),
  valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_until     TIMESTAMPTZ,                     -- NULL means still current
  supersedes_id   UUID REFERENCES public.knowledge(id),

  -- Provenance
  ingest_id       TEXT,                            -- idempotency key (from source_chunk_id or content_hash)
  source          TEXT,                            -- agent/session/tool that created this row
  created_by      TEXT NOT NULL DEFAULT 'mmcmahon',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX knowledge_embedding_idx ON public.knowledge
  USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE INDEX knowledge_status_domain_idx ON public.knowledge (status, domain, environment);
CREATE INDEX knowledge_system_idx ON public.knowledge (system, status) WHERE system IS NOT NULL;
CREATE INDEX knowledge_tags_idx ON public.knowledge USING GIN (tags);
CREATE INDEX knowledge_valid_from_idx ON public.knowledge (valid_from DESC);
CREATE INDEX knowledge_supersedes_idx ON public.knowledge (supersedes_id) WHERE supersedes_id IS NOT NULL;
CREATE INDEX knowledge_created_by_idx ON public.knowledge (created_by, status);

-- ── Duplicate-current guard ───────────────────────────────────────────────────
-- Blocks a second 'current' record for the same system + component:* tag
-- without a supersedes_id. General tags (VLAN, Production, etc.) are ignored.
-- Records with system IS NULL (study/personal content) are exempt.
-- See ADR-008 for component:* tag convention and rationale.

CREATE OR REPLACE FUNCTION public.validate_knowledge_insert()
RETURNS TRIGGER AS $$
DECLARE
  component_tag TEXT;
BEGIN
  IF NEW.status = 'current' AND NEW.system IS NOT NULL THEN
    SELECT t INTO component_tag
    FROM unnest(NEW.tags) t
    WHERE t LIKE 'component:%'
    LIMIT 1;

    IF component_tag IS NOT NULL THEN
      IF EXISTS (
        SELECT 1 FROM public.knowledge
        WHERE system = NEW.system
          AND status = 'current'
          AND tags @> ARRAY[component_tag]
          AND NEW.supersedes_id IS NULL
      ) THEN
        RAISE EXCEPTION
          'Duplicate current record blocked: system=%, component=%. Set supersedes_id to the ID being superseded.',
          NEW.system, component_tag;
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER knowledge_insert_validation
  BEFORE INSERT ON public.knowledge
  FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_insert();

-- ── Row Level Security ────────────────────────────────────────────────────────
-- Owner scoping is at the application layer via require_auth_owner() (bearer
-- token → owner binding), consistent with public.thoughts. We do not use
-- current_setting('app.owner') in RLS — incompatible with the transaction
-- pooler on port 6543. See ADR-010.

ALTER TABLE public.knowledge ENABLE ROW LEVEL SECURITY;

CREATE POLICY knowledge_service_all ON public.knowledge
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY knowledge_anon_read ON public.knowledge
  FOR SELECT TO anon USING (status = 'current');

CREATE POLICY knowledge_agent_insert ON public.knowledge
  FOR INSERT TO anon WITH CHECK (status IN ('current', 'draft'));
