"""Tests for the ingest plan/apply handshake (api/ingest_plan.py).

The gate exists because a writer cannot judge "is this an update?" without knowing what living
docs exist, and no surface could enumerate them. Two keyless rows (400a4e85, 997ce045) landed as
competitors to the living docs they should have superseded, both returning 200.

These tests cover the token contract and the decision contract without touching the database:
build_plan is stubbed where a test only cares about the gate logic. The token half is pure and
needs no stubbing at all.

Run: cd tests && ../.venv/bin/python -m pytest test_ingest_plan.py -q
"""
from __future__ import annotations

import time
from unittest.mock import patch

from api import ingest_plan as ip

OWNER = "mike.mcmahon67"
CONTENT = "# Flight Sim Rig — TQS mounting resolved\n\nAs-built, measured."
OTHER = "# Something else entirely\n\nUnrelated content."


# ── token contract ────────────────────────────────────────────────────────────

def test_token_roundtrips_for_same_content_and_owner():
    h = ip.content_hash(CONTENT)
    ok, why = ip.decode_plan_token(ip.encode_plan_token(h, OWNER), h, OWNER)
    assert ok, why


def test_token_is_bound_to_content():
    # The plan-FILE semantic: planning one document must not authorise committing another.
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    ok, why = ip.decode_plan_token(tok, ip.content_hash(OTHER), OWNER)
    assert not ok
    assert "does not match this content" in why


def test_token_is_bound_to_owner():
    h = ip.content_hash(CONTENT)
    ok, why = ip.decode_plan_token(ip.encode_plan_token(h, OWNER), h, "snapple01")
    assert not ok
    assert "different owner" in why


def test_token_expires():
    h = ip.content_hash(CONTENT)
    tok = ip.encode_plan_token(h, OWNER)
    with patch.object(ip.time, "time", return_value=time.time() + ip.PLAN_TTL_SECONDS + 1):
        ok, why = ip.decode_plan_token(tok, h, OWNER)
    assert not ok
    assert "expired" in why


def test_tampered_signature_rejected():
    h = ip.content_hash(CONTENT)
    tok = ip.encode_plan_token(h, OWNER)
    body, sig = tok.split(".", 1)
    forged = f"{body}.{'0' * len(sig)}"
    ok, why = ip.decode_plan_token(forged, h, OWNER)
    assert not ok
    assert "signature invalid" in why


def test_malformed_token_rejected_not_crashed():
    ok, why = ip.decode_plan_token("garbage", ip.content_hash(CONTENT), OWNER)
    assert not ok and "malformed" in why


# ── decision contract ─────────────────────────────────────────────────────────

def _plan(candidates=(), similar=()):
    return {
        "plan_token": "unused",
        "candidates": sorted(candidates),
        "current_state": {"living_docs_in_system": [], "similar_living_docs": list(similar)},
        "would_supersede": None,
    }


def test_apply_without_token_returns_the_plan_not_just_an_error():
    # The rejection IS the enumeration — a naive single call still learns what exists.
    with patch.object(ip, "build_plan", return_value=_plan({"flightsim-hardware"})):
        ok, err = ip.verify_apply({}, OWNER, CONTENT)
    assert not ok
    assert err["error"] == "plan_required"
    assert err["status"] == 409
    assert "flightsim-hardware" in err["plan"]["candidates"]


def test_naming_a_component_is_a_complete_decision():
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    with patch.object(ip, "build_plan", return_value=_plan({"flightsim-hardware"})):
        ok, err = ip.verify_apply(
            {"plan_token": tok, "system": "FlightSim", "component": "flightsim-hardware"},
            OWNER, CONTENT)
    assert ok, err


def test_declining_requires_naming_every_candidate():
    # Declining is deliberately not a boolean: "nah" must state the thing being ignored.
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    plan = _plan({"flightsim-hardware", "flightsim-headset-decision"})
    with patch.object(ip, "build_plan", return_value=plan):
        ok, err = ip.verify_apply({"plan_token": tok, "acknowledged_not_updating": []},
                                  OWNER, CONTENT)
    assert not ok
    assert err["error"] == "decision_required"
    assert set(err["missing"]) == {"flightsim-hardware", "flightsim-headset-decision"}


def test_partial_acknowledgement_is_rejected():
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    plan = _plan({"flightsim-hardware", "flightsim-headset-decision"})
    with patch.object(ip, "build_plan", return_value=plan):
        ok, err = ip.verify_apply(
            {"plan_token": tok, "acknowledged_not_updating": ["flightsim-hardware"]},
            OWNER, CONTENT)
    assert not ok
    assert err["missing"] == ["flightsim-headset-decision"]


def test_full_acknowledgement_passes_when_nothing_is_close():
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    plan = _plan({"flightsim-hardware"},
                 similar=[{"component_key": "flightsim-hardware", "similarity": 0.42}])
    with patch.object(ip, "build_plan", return_value=plan):
        ok, err = ip.verify_apply(
            {"plan_token": tok, "acknowledged_not_updating": ["flightsim-hardware"]},
            OWNER, CONTENT)
    assert ok, err


def test_declining_a_close_match_costs_a_written_reason():
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    plan = _plan({"flightsim-hardware"},
                 similar=[{"component_key": "flightsim-hardware", "similarity": 0.81}])
    with patch.object(ip, "build_plan", return_value=plan):
        ok, err = ip.verify_apply(
            {"plan_token": tok, "acknowledged_not_updating": ["flightsim-hardware"]},
            OWNER, CONTENT)
    assert not ok
    assert err["error"] == "decline_reason_required"

    with patch.object(ip, "build_plan", return_value=plan):
        ok, err = ip.verify_apply(
            {"plan_token": tok, "acknowledged_not_updating": ["flightsim-hardware"],
             "decline_reason": "This is a session wrap about the build, not the rig inventory."},
            OWNER, CONTENT)
    assert ok, err


def test_no_living_docs_in_scope_means_no_friction():
    # The gate only bites where a collision is actually possible.
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    with patch.object(ip, "build_plan", return_value=_plan()):
        ok, err = ip.verify_apply({"plan_token": tok}, OWNER, CONTENT)
    assert ok, err


# ── enforcement is dark by default ────────────────────────────────────────────

def test_enforcement_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("OPENBRAIN_REQUIRE_INGEST_PLAN", raising=False)
    assert ip.enforcement_enabled() is False
    for on in ("1", "true", "YES", "on"):
        monkeypatch.setenv("OPENBRAIN_REQUIRE_INGEST_PLAN", on)
        assert ip.enforcement_enabled() is True
    monkeypatch.setenv("OPENBRAIN_REQUIRE_INGEST_PLAN", "0")
    assert ip.enforcement_enabled() is False
