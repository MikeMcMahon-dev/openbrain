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
