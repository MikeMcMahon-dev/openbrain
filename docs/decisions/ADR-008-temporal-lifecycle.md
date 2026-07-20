# ADR-008: Temporal Lifecycle for Operational Knowledge

**Status:** Accepted
**Date:** 2026-05-13

## Context

`public.thoughts` has no concept of time beyond `created_at`. When operational state changes
(example: Pi-Hole moved from 192.168.100.30 to 192.168.110.30), both the old and new records
exist with equal retrieval weight. The old record actively misleads queries about current state.

Retrieval is by semantic similarity — a May 5 record and a May 8 record about the same system
component compete equally. There is no mechanism to say "this record was true then; this one
is true now."

This fails specifically for operational knowledge that changes: IP addresses, service configs,
cluster state, network topology. Study notes (stable reference material) are less affected.

## Decision

The `knowledge` table adds a temporal lifecycle layer:

```sql
status      TEXT    CHECK (status IN ('current','superseded','historical','draft'))
valid_from  TIMESTAMPTZ  DEFAULT now()
valid_until TIMESTAMPTZ  -- NULL means still current
supersedes_id UUID REFERENCES public.knowledge(id)
```

**Status semantics:**
- `current` — the authoritative present-tense record for this system/component
- `superseded` — replaced by a newer record via the supersession chain; `valid_until` is set
- `historical` — migrated from `thoughts`; not reviewed for currency; do not treat as current state
- `draft` — staged but not yet approved for querying

**Deduplication rule — `component:*` canonical tag:**

The duplicate-prevention trigger checks for conflicting `current` records using the `system`
column and a canonical `component:<name>` tag, **not** general tag overlap. Example tags:
`['Switch', 'component:L3208', 'VLAN']`.

The trigger blocks a new `current` INSERT for the same `system` AND same `component:*` tag
without a `supersedes_id`. General tags like `'VLAN'` or `'Production'` do not trigger the
deduplication check — only the `component:*` prefix tag is the deduplication key.

This prevents false positives where two distinct components of the same system (e.g.,
a switch and a firewall, both tagged `['Switch']`) would incorrectly block each other.

**Refined trigger logic:**

```sql
CREATE OR REPLACE FUNCTION public.validate_knowledge_insert()
RETURNS TRIGGER AS $$
DECLARE
  component_tag TEXT;
BEGIN
  IF NEW.status = 'current' AND NEW.system IS NOT NULL THEN
    -- Extract the component:* canonical tag from the new record
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
          'Cannot create duplicate current record for system=% component=% without supersession chain.',
          NEW.system, component_tag;
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

Records for `system IS NULL` (study content, personal notes) have no deduplication constraint —
unlimited `current` records with the same tags are allowed. Deduplication is only meaningful
for operational state where a single authoritative answer is expected.

## Consequences

- Retrieval filters default to `status='current'` — stale state is excluded without manual filtering
- Supersession chain (`supersedes_id`) provides a full audit trail of state changes
- `valid_until` is set atomically when a record is superseded (via `confirm_supersession`)
- Migrated `thoughts` records use `status='historical'` — they do not pollute current-state queries
- Study/personal content (system IS NULL) has no deduplication constraint — this is correct
- `component:*` tag is required on any operational record that should participate in deduplication
- Records without a `component:*` tag are allowed any number of `current` entries for the same system

## Update (2026-07-19) — auto-supersession on ingest

The original design required an explicit two-step `propose_supersession` → `confirm_supersession`
to retire a prior `current` record. In practice that step was never run: every "current-state"
note was ingested as a *new* `current` row without a `component:*` identity, so the dedup trigger
never fired and stale state accumulated alongside live state (RRF ranking is recency-blind, so an
old note could outrank the authoritative one). Root-caused 2026-07-19.

`api.knowledge_ingest.write_knowledge` now performs **living-doc auto-supersession** on ingest
(`auto_supersede=True` default): when a `current` write carries a `component:*` tag and a non-null
`system`, any existing `current` row(s) for that exact `(system, component)` are flipped to
`superseded` (stamping `valid_until`) and the new row is linked via `supersedes_id` — atomically,
in the same transaction as the INSERT. This makes an "overwrite" replace in place instead of piling
up a second competitor. The behavior is a **no-op without a `component:*` tag**, so event/history
notes (session wraps, incident reports) remain append-only and unaffected. `component:*` is thus the
single signal that separates *living docs* (keyed, replace-in-place) from *event log* (append-only) —
the lightweight alternative to physically splitting the table.

Producers set the identity via `scripts/ob_ingest.py --component <name>` (adds `component:<name>`);
the payload also accepts `tags` (threaded through `handle_ingest` → `_write_text_ingest` →
`_write_text_ingest_knowledge`) and `auto_supersede: false` to opt out. When a duplicate is written
with auto-supersede off, the dedup trigger surfaces a clean **409 conflict** pointing at the
supersession path (the 409 matcher was made case-insensitive to catch the deployed trigger's wording,
which had drifted from the message quoted above).
