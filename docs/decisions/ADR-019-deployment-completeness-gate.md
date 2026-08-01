# ADR-019: Deployment-completeness gate — no capability without a caller

**Status:** Accepted (mechanical half shipped: `scripts/capability_audit.py`, PR #78;
persona at `home-lab/.claude/agents/pm.md`)
**Date:** 2026-07-31
**Relates to:** ADR-018 (the motivating case). 018 cites 019 as the root cause behind the
`system` plumbing gap, the null-`system` identity (Q1b), and the P5 reframe; 019 cites 018
as the case that surfaced the pattern.

## Context — a pattern broader than the ADR that surfaced it

OpenBrain 2.0 shipped five capabilities wired at the schema layer and at **no caller**:

| capability | organic utilisation | wired at |
|---|---|---|
| `valid_from` (fact-onset) | **0%** | schema only |
| `supersedes_id` | **0%** | schema only |
| `valid_until` | 1% | schema + `auto_supersede` write |
| `component_key` (as a tag) | 1% | schema (unvalidated) |
| `system` | **14%** | schema + write; **not the ingest surface** |
| `environment` (control) | 100% | end-to-end |

Each looked deployed and wasn't: the field existed, but no ingest surface, API param, or CLI
flag let a caller set it, so it silently took its default and sat inert until discovered months
later. This is **"capability without a caller."** It is not an ADR-018 problem — `supersedes_id`
at 0% is a pre-existing gap unrelated to supersession-as-transition-records — it is a *process*
gap: a change can land at one layer and be called "deployed" with no check that the path to
actually using it was completed. It will recur without a gate, and the gate must outlive
ADR-018's eventual supersession.

**`system` at 14% is the number to watch.** A partially-populated field is *more* dangerous than
a zero: 0% is visibly unused, while 14% looks used and is actually inferred, inconsistently. That
is precisely how the `(system, component)` identity became unsatisfiable on purpose.

## Decision

A two-part gate, mechanical first.

### 1. The rubric — every new capability traces through every layer

For each capability a change adds, mark each layer **✓ wired**, **✗ gap**, **N/A**, or
**deferred (reason + tracking)**:

`schema → write (sets it, not just defaults it) → ingest/caller surface (a caller can set it) →
read (a consumer uses it) → enforce (constraint/validation) → test → docs`

The **ingest/caller surface** is the layer that failed all five times. A schema-✓ / surface-✗ is
the signature half-deploy: flag it loudly.

### 2. The mechanical gate (load-bearing) — `scripts/capability_audit.py`

Measures per-capability utilisation against the live DB; **FAILs (exit 1) on any sub-3%
capability not in `capability_audit.allow.json`.** The allowlist is the *registry of known gaps*:
each entry carries a reason and a tracking reference. A new orphan cannot merge unless it is
wired **or** registered — registering the gap *is* the completeness discipline. `make
capability-audit`. Extend the `CAPABILITIES` list when a change adds a field worth watching.

### 3. The PM reviewer persona — `home-lab/.claude/agents/pm.md`

Applies the rubric at ADR/PR/feature time. Authority is **procedural + mechanical, not
hierarchical** (the harness has no agent hierarchy): it verifies claims against code/schema/DB
(never a worker's report), blocks on unregistered gaps, and escalates integrity issues to Mike.

**Design constraint (Chat):** the escalation trigger is tied to **mechanical audit output**, not
the persona's own assessment. The audit is the load-bearing half. A reviewer that escalates on
its own judgment is a judgment call wearing a role name — which is the thing the mechanical scan
exists to replace. The persona wraps judgment (the rubric layers a scan can't measure: is it
*documented*? is the *read path* wired?) around a mechanical core, and its hard STOPs come from
the scan.

## Consequences

- **Positive:** a half-deploy fails a merge instead of being discovered months later; every
  shipped-but-unwired capability is registered with a reason and a tracking ref (an audit trail
  that didn't exist); the "ingest surface" layer that failed five times is now an explicit gate.
- **Cost:** the `CAPABILITIES` list and allowlist need maintenance as the schema grows; the
  audit is OpenBrain-specific today (it queries the OB DB) though the rubric and persona are
  home-lab-wide.
- **Scope:** the audit currently covers `public.knowledge`; extend per table as capabilities are
  added. The persona is reusable across the home lab, not just OpenBrain.

## Not doing

- **A generic schema-introspection auditor.** Utilisation is capability-specific (organic
  `valid_from` must exclude migration writes; `component_key` is a tag pattern), so a curated
  `CAPABILITIES` list beats a blind "every nullable column" scan that would drown in false
  positives. Revisit if the curated list becomes unwieldy.
- **Blocking on `system`'s 14%** today — it is registered and tracked by ADR-018 P2. The gate
  fires on *new* orphans, not the known backlog.
