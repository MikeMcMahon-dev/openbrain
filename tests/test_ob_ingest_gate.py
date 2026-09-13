"""Client-side gate in scripts/ob_ingest.py.

The server is the real authority, but it only enforces when OPENBRAIN_REQUIRE_INGEST_PLAN is on.
Until that flips, this script IS the gate on the path Mike wraps sessions through — so its
fail-closed behavior needs a test of its own. It earned one: the first version read the decline
threshold from the plan and treated "server did not send one" as "nothing is close", which let a
0.777 near-duplicate of an existing living doc into the vault with no stated reason.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ob_ingest", Path(__file__).resolve().parent.parent / "scripts" / "ob_ingest.py")
ob = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ob)


def _plan(similar=(), threshold=None):
    plan = {"current_state": {"similar_living_docs": [
        {"component_key": k, "similarity": v} for k, v in similar]}}
    if threshold is not None:
        plan["decline_reason_threshold"] = threshold
    return plan


def test_above_threshold_is_close():
    close, known = ob.close_matches(_plan([("dns-current-state", 0.777)], threshold=0.75))
    assert known is True
    assert [d["component_key"] for d in close] == ["dns-current-state"]


def test_below_threshold_is_not_close():
    close, known = ob.close_matches(_plan([("dns-current-state", 0.528)], threshold=0.75))
    assert known is True
    assert close == []


def test_missing_threshold_fails_closed():
    """No declared bar => every suggestion counts, so a reason is still demanded."""
    close, known = ob.close_matches(_plan([("dns-current-state", 0.777),
                                           ("coredns-config-visibility", 0.635)]))
    assert known is False
    assert len(close) == 2, "a gate that cannot evaluate its rule must not wave the write through"


def test_missing_threshold_with_no_suggestions_stays_quiet():
    """Fail-closed must not mean fail-noisy: nothing similar, nothing to justify."""
    close, known = ob.close_matches(_plan([]))
    assert known is False
    assert close == []


# ── tag pre-check (ADR-012) ───────────────────────────────────────────────────


def test_unknown_tags_come_from_the_server_report():
    plan = {"tags": {"canonical": ["K8s"], "unknown": ["kubez"], "vocabulary_source": "db"}}
    unknown, said = ob.unknown_tags(plan, ["K8s", "kubez", "shape:note"])
    assert said is True
    assert unknown == ["kubez"]


def test_all_canonical_is_quiet():
    plan = {"tags": {"canonical": ["K8s", "DNS"], "unknown": [], "vocabulary_source": "db"}}
    unknown, said = ob.unknown_tags(plan, ["K8s", "DNS", "component:x"])
    assert said is True
    assert unknown == []


def test_missing_tag_report_fails_closed():
    """A server that cannot say which tags it will park gets no benefit of the doubt: every
    descriptive tag is unverified. Namespaced tags are never vocabulary and stay out of it."""
    unknown, said = ob.unknown_tags({}, ["K8s", "kubez", "shape:note", "component:x"])
    assert said is False
    assert unknown == ["K8s", "kubez"]


def test_missing_tag_report_with_no_descriptive_tags_stays_quiet():
    unknown, said = ob.unknown_tags({}, ["shape:note", "component:x"])
    assert said is False
    assert unknown == []
