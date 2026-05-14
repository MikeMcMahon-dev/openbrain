-- OB2 Migration 002: public.wiki_pages table
-- Stage 2 — additive only, does NOT touch public.thoughts
-- Apply after 001_knowledge_table.sql

-- ── Table ─────────────────────────────────────────────────────────────────────

CREATE TABLE public.wiki_pages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_name       TEXT UNIQUE NOT NULL,            -- e.g. "spectrenet-current-state"
  page_type       TEXT NOT NULL
                    CHECK (page_type IN ('system-state', 'pending-tasks', 'topology', 'summary', 'event-log')),
  content         TEXT NOT NULL,                   -- compiled markdown — never manually edited
  compiled_from   UUID[] NOT NULL DEFAULT '{}',    -- knowledge row IDs that contributed to this page
  domain          TEXT NOT NULL,
  system          TEXT,
  generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  is_stale        BOOLEAN NOT NULL DEFAULT false   -- set true when a contributing knowledge row changes
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX wiki_pages_system_idx ON public.wiki_pages (system, page_type) WHERE system IS NOT NULL;
CREATE INDEX wiki_pages_stale_idx ON public.wiki_pages (is_stale, generated_at);
CREATE INDEX wiki_pages_domain_idx ON public.wiki_pages (domain, page_type);

-- ── Row Level Security ────────────────────────────────────────────────────────

ALTER TABLE public.wiki_pages ENABLE ROW LEVEL SECURITY;

CREATE POLICY wiki_pages_service ON public.wiki_pages
  FOR ALL TO service_role USING (true) WITH CHECK (true);

CREATE POLICY wiki_pages_anon_read ON public.wiki_pages
  FOR SELECT TO anon USING (true);
