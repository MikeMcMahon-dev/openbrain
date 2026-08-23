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

import json
import os
import time
from unittest.mock import patch

import pytest

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


# ── the real plan must survive JSON serialization ─────────────────────────────
#
# Regression for a bug that shipped: every test above stubs build_plan, so the DB -> JSON path
# had zero coverage. Postgres round()/extract() returns numeric -> psycopg Decimal, and Decimal
# is not JSON-serializable. The plan built correctly in Python and then 500'd the instant an MCP
# surface serialized it — "Object of type Decimal is not JSON serializable" on every call,
# including a three-word payload. A stub cannot catch a type that only exists on the wire.

@pytest.mark.skipif(not os.getenv("SUPABASE_DB_URL"), reason="needs the live vault")
def test_real_plan_is_json_serializable():
    plan = ip.build_plan("Test.", "mike.mcmahon67",
                         system="FlightSim", component="flightsim-hardware")
    json.dumps(plan)  # raises TypeError on any Decimal that escapes the SQL casts


@pytest.mark.skipif(not os.getenv("SUPABASE_DB_URL"), reason="needs the live vault")
def test_real_plan_numeric_fields_are_native_types():
    # Assert the types directly too — json.dumps alone would still pass if a future field
    # were silently stringified instead of cast.
    plan = ip.build_plan("Flight sim rig throttle mounting hardware.", "mike.mcmahon67",
                         system="FlightSim")
    for d in plan["current_state"]["living_docs_in_system"]:
        assert isinstance(d["age_days"], int), f"age_days is {type(d['age_days']).__name__}"
    for d in plan["current_state"]["similar_living_docs"]:
        assert isinstance(d["similarity"], float), \
            f"similarity is {type(d['similarity']).__name__}"


# ── enforcement is dark by default ────────────────────────────────────────────

def test_enforcement_is_off_unless_explicitly_enabled(monkeypatch):
    monkeypatch.delenv("OPENBRAIN_REQUIRE_INGEST_PLAN", raising=False)
    assert ip.enforcement_enabled() is False
    for on in ("1", "true", "YES", "on"):
        monkeypatch.setenv("OPENBRAIN_REQUIRE_INGEST_PLAN", on)
        assert ip.enforcement_enabled() is True
    monkeypatch.setenv("OPENBRAIN_REQUIRE_INGEST_PLAN", "0")
    assert ip.enforcement_enabled() is False


# --- component identity: the gate must read both spellings -------------------------------------
# ob_ingest --component sends the ADR-008 `component:*` TAG and no `component` field. The gate
# originally short-circuited on the field alone, so a declared living-doc update was answered
# with decision_required. Caught by a pre-merge enforcement trial, never in production, because
# enforcement has never been on.

def test_component_identity_reads_field():
    assert ip.component_identity({"component": "dns-current-state"}) == "dns-current-state"


def test_component_identity_reads_adr008_tag():
    assert ip.component_identity(
        {"tags": ["SpectreNet", "component:dns-current-state"]}) == "dns-current-state"


def test_component_identity_absent():
    assert ip.component_identity({"tags": ["SpectreNet", "shape:note"]}) is None


def test_apply_accepts_a_tag_declared_update():
    """The ob_ingest --component payload shape: tag only, no `component` field."""
    tok = ip.encode_plan_token(ip.content_hash(CONTENT), OWNER)
    plan = _plan({"flightsim-hardware"}, [{"component_key": "flightsim-hardware",
                                           "similarity": 0.9}])
    with patch.object(ip, "build_plan", return_value=plan):
        ok, err = ip.verify_apply(
            {"plan_token": tok, "system": "SpectreNet",
             "tags": ["component:dns-current-state"]}, OWNER, CONTENT)
    assert ok, f"a tag-declared update must pass the gate, got {err}"


def test_plan_token_survives_trailing_whitespace_across_surfaces():
    """Every surface must hash the SAME normalization of the content.

    The apply path hashes source.strip(). A surface that hashed the raw string minted tokens that
    failed at apply for any document ending in a newline — which is most of them. This pins the
    normalization so a new surface cannot quietly pick a different one.
    """
    raw = "A living doc update from a client.\n"
    tok = ip.encode_plan_token(ip.content_hash(raw.strip()), OWNER)
    ok, why = ip.decode_plan_token(tok, ip.content_hash(raw.strip()), OWNER)
    assert ok, why
    stale = ip.encode_plan_token(ip.content_hash(raw), OWNER)
    ok, _ = ip.decode_plan_token(stale, ip.content_hash(raw.strip()), OWNER)
    assert not ok, "an unstripped-hash token must not validate — that was the latent bug"
