# P2 — `system` namespace vocabulary proposal

**For:** Mike's taxonomy call (precondition on migration 006 / the P2 re-key)
**From:** CC, overnight 2026-08-01
**Decided:** `system` is a general **namespace**, not infra-systems (your ruling). This lists
the seed and asks you to bless two new values before the two null-system rows are re-keyed.

## Confirmed — already in use, keep as-is (seeds `system_vocabulary`)

| `system` | rows | holds |
|---|---|---|
| `SpectreNet` | 36 | the home network (infrastructure) |
| `PMX-01` | 1 | the Proxmox virtualization host (infrastructure) |
| `OpenBrain` | 49 | the RAG knowledge system (project) |
| `Annie` | 34 | Annie's Ubuntu/Linux study curriculum (learning track) |

These are already seeded in `api/canonical_systems.py` + migration 006 section B.

## Needs your call — the two null-`system` component rows to be re-keyed

Both are `domain=Personal`, both are living docs (component-keyed), both currently unprotected
because `system` is null:

| component | doc | proposed `system` | why |
|---|---|---|---|
| `flightsim-hardware` | "Flight Sim Rig — Hardware & Mounting (CURRENT)" | **`FlightSim`** | a distinct personal-project namespace; matches the infra/project naming feel |
| `mikemcmahon-dev-design` | "mikemcmahon.dev — Site Design Notes" | **`MikeMcMahon-Dev`** | the portfolio site as its own namespace (vs the `PortfolioBlog` *tag* which already exists) |

Alternatives if you'd rather: `FlightSim` → `SimRig`; `MikeMcMahon-Dev` → `Portfolio` or `Website`.

## What happens after you decide

1. Add the two blessed values to `api/canonical_systems.py` **and** migration 006 section B
   (the `INSERT INTO system_vocabulary` — I left a commented placeholder there).
2. Apply migration 006 (schema DDL — your sign-off).
3. Re-key the two rows: `UPDATE knowledge SET system='<value>' WHERE …component…` — the
   `component_requires_system` CHECK (added NOT VALID) validates each as you make it.
4. `ALTER TABLE public.knowledge VALIDATE CONSTRAINT component_requires_system;` — full
   enforcement once the two are clean.

The standing P0 monitor (`make capability-audit`) will show `null-system component rows` drop
from **2 → 0** when this is done — that's the acceptance signal.
