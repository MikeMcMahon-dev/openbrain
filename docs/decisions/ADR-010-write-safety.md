# ADR-010: Write Safety — INSERT-Only Agents, Two-Step Supersession

**Status:** Accepted
**Date:** 2026-05-13

## Context

Agents (Claude Code sessions, Custom GPTs, MCP tools) have direct write access to the
knowledge database. Unrestricted write access allows agents to corrupt operational state:
an agent could accidentally overwrite current state with stale data, delete records, or
mark its own insertions as authoritative `current` without human review.

The existing `thoughts` table has no write safety layer — any authenticated caller with
the service role can UPDATE or DELETE any row.

Two supersession models were considered:

1. **Single-step** — agent inserts a new record with `supersedes_id` pointing to the old
   record; the system automatically marks the old record `superseded` via an AFTER INSERT trigger
2. **Two-step** — agent proposes a supersession; human confirms; the system commits both
   sides of the transaction atomically via the service role

## Decision

**Two-step supersession, service role only.**

### Agent permissions (via RLS)

Agents are restricted to INSERT-only on `public.knowledge`:

```sql
CREATE POLICY knowledge_agent_insert ON public.knowledge
  FOR INSERT TO anon
  WITH CHECK (status IN ('current', 'draft'));
```

No UPDATE, no DELETE. An agent cannot modify existing records after insert.

### Supersession flow

**Step 1 — Propose:** Agent calls `POST /api/propose_supersession` with:
- `supersedes_id` — the ID of the record being superseded
- `content` and taxonomy fields for the replacement record

The API creates the replacement record with `status='draft'` and returns `proposal_id`.
The old record remains `status='current'` — no change yet.

**Step 2 — Confirm:** Human calls `POST /api/confirm_supersession` with `proposal_id`.

The API executes atomically via the service role in a single transaction:
```sql
BEGIN;
UPDATE public.knowledge
  SET status = 'superseded', valid_until = now()
  WHERE id = <old_id>;
UPDATE public.knowledge
  SET status = 'current'
  WHERE id = <new_draft_id>;
COMMIT;
```

Both updates happen in the same transaction — there is no window where neither or both
records are `current`.

### Why not an AFTER INSERT trigger for automatic supersession

An AFTER INSERT trigger would allow an agent to self-approve supersession by simply
inserting a record with `supersedes_id` set. The two-step process exists precisely to
require human involvement in state transitions. A trigger would bypass this.

The confirm step requires a `POST` to a separate endpoint with the `proposal_id` — this
is an action a human must take explicitly, not something an agent can chain into its
ingest workflow.

### RLS and owner scoping

Owner scoping remains at the application layer via `require_auth_owner()`, consistent with
the existing `thoughts` table pattern. The `knowledge_anon_read` RLS policy exposes all
`status='current'` records to any authenticated caller:

```sql
CREATE POLICY knowledge_anon_read ON public.knowledge
  FOR SELECT TO anon USING (status = 'current');
```

Owner filtering is enforced by the bearer token → owner binding (`OPENBRAIN_TOKEN_OWNER_MAP`),
not at the Postgres RLS layer. This is intentional:

- Adding `created_by = current_setting('app.owner')` to the RLS policy requires the
  `app.owner` session variable to be set on every connection
- The connection pooler (port 6543) does not guarantee session variable persistence across
  pooled connections — a variable set on connection A may not be present on connection B
- Application-layer enforcement via `require_auth_owner()` is consistent with how `thoughts`
  is protected today and avoids this pooler complexity

This design is acceptable given that all tenants in the current deployment are family members
with explicit knowledge that others have separate isolated vaults. If multi-tenant isolation
requirements change, revisit this decision before expanding beyond the family deployment.

## Consequences

- Agents cannot corrupt existing records — INSERT only, no UPDATE/DELETE
- Supersession requires human confirmation — no agent can unilaterally change current state
- Atomic transaction prevents split-brain state (two simultaneous `current` records)
- Draft records are created first — humans can inspect proposed changes before committing
- Owner isolation via application layer is consistent with `thoughts` and avoids pooler issues
- Connection pooler (port 6543) remains safe — no session variable dependency in RLS
