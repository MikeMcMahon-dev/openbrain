-- OB2 Migration 014: add `Career` to the system namespace vocabulary
--
-- WHY: career notes (job search, offers, negotiation) could not use the living-doc
-- supersession mechanism. A `component:*` identity requires `system` (006's
-- component_requires_system CHECK), and `system` must be in public.system_vocabulary
-- (006's knowledge_system_validation trigger). With no Career namespace, every update to an
-- offer's current state stacked as an append-only note instead of retiring the previous one.
--
-- Data only: one vocabulary row. No table, no grants (see TEMPLATE.sql — this CREATEs nothing).
-- Source of truth stays api/canonical_systems.py CANONICAL_SYSTEMS; this keeps the DB in sync.
-- Idempotent: ON CONFLICT DO NOTHING.
--
-- APPLY ORDER: this BEFORE deploying the code that advertises `Career` in the MCP/Action
-- enums. The other way round, a client offered `Career` hits the trigger and the write fails.
--
-- VERIFY: SELECT EXISTS (SELECT 1 FROM public.system_vocabulary WHERE system = 'Career');
--
-- ROLLBACK (only if no knowledge row uses it — the trigger fires on INSERT/UPDATE, not on a
-- vocabulary delete, so check first):
--   SELECT count(*) FROM public.knowledge WHERE system = 'Career';   -- must be 0
--   DELETE FROM public.system_vocabulary WHERE system = 'Career';

INSERT INTO public.system_vocabulary (system) VALUES ('Career')
ON CONFLICT (system) DO NOTHING;
