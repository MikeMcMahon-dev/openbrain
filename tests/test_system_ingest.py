"""Tests for explicit `system` at the ingest surface (ADR-018 P2 / ADR-019).

`system` was settable in write_knowledge() but exposed by no ingest surface, so it was
inferred unreliably and the (system, component) supersession identity was unsatisfiable on
purpose. These prove: system_override flows through, a component:* write REQUIRES a system,
and a non-canonical system warns. Fully mocked — no DB, no embeddings.

Run: cd tests && python -m pytest test_system_ingest.py -q
"""
from __future__ import annotations

from unittest.mock import patch

from api import _openbrain_api as ob
from api.canonical_systems import CANONICAL_SYSTEMS, is_canonical_system


def _tax(system=None, tags=None):
    def _fn(**_kw):
        return {"domain": "Network", "environment": "Production",
                "system": system, "tags": list(tags or []), "shape": "note"}
    return _fn


def _write_capturing(captured):
    def _fn(content, owner, **kw):
        captured.update(kw)
        return {"status": "accepted"}
    return _fn


def _run(captured, *, tax_system=None, tax_tags=None, warnings=None, **kwargs):
    with patch("api.knowledge_ingest.write_knowledge", side_effect=_write_capturing(captured)), \
         patch("api.taxonomy_map.map_to_taxonomy", side_effect=_tax(tax_system, tax_tags)), \
         patch.object(ob, "_honor_owners", return_value={"mike.mcmahon67"}):
        return ob._write_text_ingest_knowledge(
            "some content", "mike.mcmahon67", "subj", "topic", warnings=warnings, **kwargs)


def test_canonical_systems_membership():
    assert is_canonical_system("SpectreNet")
    assert not is_canonical_system("bogus")
    assert not is_canonical_system(None)
    assert "OpenBrain" in CANONICAL_SYSTEMS and "Annie" in CANONICAL_SYSTEMS


def test_system_override_flows_to_write():
    cap = {}
    err = _run(cap, system_override="SpectreNet")
    assert err is None
    assert cap["system"] == "SpectreNet"          # honored, not the inferred None


def test_component_without_system_is_rejected():
    cap = {}
    err = _run(cap, tax_tags=["component:dns-current-state"])   # no system anywhere
    assert err is not None and "requires an explicit `system`" in err
    assert cap == {}                               # write_knowledge never called


def test_component_with_explicit_system_succeeds():
    cap = {}
    err = _run(cap, tax_tags=["component:dns-current-state"], system_override="SpectreNet")
    assert err is None
    assert cap["system"] == "SpectreNet"
    assert "component:dns-current-state" in cap["tags"]


def test_non_canonical_system_warns_but_writes():
    cap, warns = {}, []
    err = _run(cap, system_override="Galaxy", warnings=warns)
    assert err is None
    assert cap["system"] == "Galaxy"               # written (DB CHECK enforces later)
    assert any("not a canonical namespace" in w for w in warns)


def test_no_component_no_system_is_fine():
    cap = {}
    err = _run(cap)                                # no component, no system -> allowed
    assert err is None
    assert cap["system"] is None
