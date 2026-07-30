-- OB2 Migration 005: public.knowledge_chunked  (ADR-017 heading-based chunking)
-- NOT YET APPLIED — clone-and-cut-over strategy. Live public.knowledge is untouched;
-- reads move here behind a flag, rollback = flip the flag. Apply only on sign-off.
-- Apply via Supabase Dashboard SQL editor or: supabase db push
--
-- Differences from public.knowledge:
--   + document_id / chunk_index / heading  (chunk identity within a parent document)
--   * the duplicate-current guard is per-DOCUMENT, not per-row: a living doc is now a
--     SET of current chunks sharing one (system, component), so N current rows per
--     identity is correct — what must stay unique is one current DOCUMENT per identity.

-- ── Table ─────────────────────────────────────────────────────────────────────

CREATE TABLE public.knowledge_chunked (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  content         TEXT NOT NULL,                   -- clean section text (display / fetch)
  embedding       vector(1536),                    -- of (title + content); see ADR-017

  -- Chunk identity (NEW)
  document_id     UUID NOT NULL,                   -- parent document; chunks of a doc share it
  chunk_index     INTEGER NOT NULL,                -- order within the document
  heading         TEXT,                            -- section heading (also a skim label)

  -- Multi-dimensional taxonomy (inherited from the parent write)
  domain          TEXT NOT NULL,
  environment     TEXT NOT NULL DEFAULT 'Study',
  system          TEXT,
  tags            TEXT[] NOT NULL DEFAULT '{}',

  -- Temporal lifecycle
  status          TEXT NOT NULL DEFAULT 'current'
                    CHECK (status IN ('current', 'superseded', 'historical', 'draft')),
  valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
  valid_until     TIMESTAMPTZ,
  supersedes_id   UUID,                            -- prior DOCUMENT id (not per-chunk)

  -- Provenance
  ingest_id       TEXT,                            -- deterministic per (content, owner, ..., chunk_index)
  source          TEXT,
  created_by      TEXT NOT NULL DEFAULT 'mmcmahon',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX knowledge_chunked_embedding_idx ON public.knowledge_chunked
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
-- HNSW (pgvector 0.8 on PG17), NOT ivfflat: it sidesteps the probes-starvation recall
-- bug that bit public.knowledge (ADR-015) — no per-query `probes` to tune, high recall by
-- default. Chunking multiplies row count so recall matters more here; HNSW handles it.
-- Tune query-time `hnsw.ef_search` (default 40) only if the eval shows a recall gap.

CREATE INDEX knowledge_chunked_status_domain_idx ON public.knowledge_chunked (status, domain, environment);
CREATE INDEX knowledge_chunked_system_idx ON public.knowledge_chunked (system, status) WHERE system IS NOT NULL;
CREATE INDEX knowledge_chunked_tags_idx ON public.knowledge_chunked USING GIN (tags);
CREATE INDEX knowledge_chunked_created_by_idx ON public.knowledge_chunked (created_by, status);
CREATE INDEX knowledge_chunked_document_idx ON public.knowledge_chunked (document_id, chunk_index);

-- Idempotency: re-running the backfill / re-ingesting unchanged content must not
-- duplicate a chunk. A document's chunk positions are unique within the document.
CREATE UNIQUE INDEX knowledge_chunked_doc_chunk_uniq
  ON public.knowledge_chunked (document_id, chunk_index);

-- ── Per-DOCUMENT duplicate-current guard ──────────────────────────────────────
-- Unlike public.knowledge (one current ROW per system+component), a chunked living
-- doc is many current rows sharing a document_id. So: block a new current chunk only
-- when a current chunk for the same (system, component) belongs to a DIFFERENT
-- document and no supersedes_id is set. Same-document chunks are fine; the ADR-008
-- retire-all-then-insert-all transaction retires the prior document's chunks first,
-- so inserting the new document's chunks never trips this.

CREATE OR REPLACE FUNCTION public.validate_knowledge_chunked_insert()
RETURNS TRIGGER AS $$
DECLARE
  component_tag TEXT;
BEGIN
  IF NEW.status = 'current' AND NEW.system IS NOT NULL THEN
    SELECT t INTO component_tag
    FROM unnest(NEW.tags) t
    WHERE t LIKE 'component:%'
    LIMIT 1;

    IF component_tag IS NOT NULL AND NEW.supersedes_id IS NULL THEN
      IF EXISTS (
        SELECT 1 FROM public.knowledge_chunked
        WHERE system = NEW.system
          AND status = 'current'
          AND tags @> ARRAY[component_tag]
          AND document_id <> NEW.document_id
      ) THEN
        RAISE EXCEPTION
          'Duplicate current document blocked: system=%, component=%. Supersede the prior document first.',
          NEW.system, component_tag;
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER knowledge_chunked_insert_validation
  BEFORE INSERT ON public.knowledge_chunked
  FOR EACH ROW EXECUTE FUNCTION public.validate_knowledge_chunked_insert();

-- ── Row Level Security (mirrors public.knowledge / ADR-010) ────────────────────

ALTER TABLE public.knowledge_chunked ENABLE ROW LEVEL SECURITY;

CREATE POLICY knowledge_chunked_service_all ON public.knowledge_chunked
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY knowledge_chunked_anon_read ON public.knowledge_chunked
  FOR SELECT TO anon USING (status = 'current');

CREATE POLICY knowledge_chunked_agent_insert ON public.knowledge_chunked
  FOR INSERT TO anon WITH CHECK (status IN ('current', 'draft'));
