"""Canonical `system` namespace vocabulary (ADR-018 P2).

`system` is a general NAMESPACE, not infrastructure-systems (Mike's ruling, 2026-08-01):
it groups content by top-level namespace — infrastructure, projects, and learning tracks
alike. An infra-only reading can't house `Annie` (her Ubuntu/Linux study curriculum), which
is the forcing case.

`system` is REQUIRED whenever a `component:*` identity tag is present — that pair is the
supersession pivot, and a null `system` is what made the identity unsatisfiable on purpose
(ADR-019 "capability without a caller"). It is validated against this set at the ingest
surface; the DB CHECK + partial unique index enforce it at write time once P2's migration
lands.

Keep this the single source of truth for the seed of `public.system_vocabulary`.
"""
from __future__ import annotations

# fmt: off
CANONICAL_SYSTEMS: frozenset[str] = frozenset({
    # ── infrastructure ──
    "SpectreNet",      # the home network
    "PMX-01",          # the Proxmox virtualization host
    # ── projects ──
    "OpenBrain",       # the RAG knowledge system
    # ── learning tracks ──
    "Annie",           # Annie's Ubuntu/Linux study curriculum
    # NOTE: the two currently null-`system` component rows (flightsim-hardware,
    # mikemcmahon-dev-design) need a namespace before the P2 re-key. Proposed values are
    # in the P2 handoff for Mike's taxonomy call; add them here once blessed, then re-key.
})
# fmt: on


def is_canonical_system(value: str | None) -> bool:
    """True if `value` is a registered namespace. None/blank is not canonical."""
    return bool(value) and value in CANONICAL_SYSTEMS
