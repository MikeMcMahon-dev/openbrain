"""Pure-function tests for api.taxonomy_map.map_to_taxonomy (OB2 Workstream B).

Covers representative rows from docs/migrations/001_domain_discovery.md, the
curation drop cases (smoke-test/malformed subjects, smoke-test owner), and the
personal/mental-health -> Personal/Archive override. No DB required.

Run from repo root:
    .venv/bin/python -m pytest tests/test_taxonomy_map.py -q
"""

from __future__ import annotations

import pytest

from api.taxonomy_map import (
    VALID_DOMAINS,
    VALID_ENVIRONMENTS,
    VALID_SHAPES,
    derive_shape,
    map_to_taxonomy,
)


def m(subject, topic="", owner="mike.mcmahon67", source_type="text", content="x"):
    return map_to_taxonomy(subject, topic, owner, source_type, content)


# ── Invariants ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("subject,topic,owner,source_type", [
    ("Home Network", "NAS Benchmark", "mike.mcmahon67", "text"),
    ("Science", "Cell Division", "anneliesepaige", "obsidian"),
    ("smoke test", "2026-03-29", "mike.mcmahon67", "text"),
    (None, None, None, "slack"),
    ("totally unknown subject xyz", "stuff", "mike.mcmahon67", "text"),
])
def test_result_always_has_full_contract(subject, topic, owner, source_type):
    r = map_to_taxonomy(subject, topic, owner, source_type, "content")
    assert set(r) == {"domain", "environment", "system", "tags", "shape", "drop", "reason"}
    assert isinstance(r["tags"], list)
    assert isinstance(r["drop"], bool)
    assert r["shape"] in VALID_SHAPES


def test_non_dropped_rows_have_valid_taxonomy():
    r = m("Home Network", "NAS Benchmark")
    assert r["drop"] is False
    assert r["domain"] in VALID_DOMAINS
    assert r["environment"] in VALID_ENVIRONMENTS


def test_dropped_rows_never_invent_a_domain():
    r = m("smoke test", "2026-03-29")
    assert r["drop"] is True
    assert r["domain"] is None
    assert r["environment"] is None
    assert r["system"] is None
    assert r["tags"] == []


# ── Network / Production ─────────────────────────────────────────────────────

def test_home_network_to_network_production_spectrenet():
    r = m("Home Network", "SpectreNet Handoff")
    assert (r["domain"], r["environment"], r["system"]) == ("Network", "Production", "SpectreNet")


def test_spectrenet_subject():
    r = m("SpectreNet", "Infrastructure IPs")
    assert r["domain"] == "Network" and r["system"] == "SpectreNet"


def test_homelab_to_pmx01():
    r = m("Homelab", "Proxmox Inventory")
    assert (r["domain"], r["environment"], r["system"]) == ("Network", "Production", "PMX-01")
    assert "Proxmox" in r["tags"]


# ── Kubernetes ───────────────────────────────────────────────────────────────

def test_kubernetes_to_k8s_lab():
    r = m("Kubernetes", "MetalLB kube-proxy nftables K8s 1.35")
    assert (r["domain"], r["environment"]) == ("K8s", "Lab")


def test_cka_training_is_study_not_k8s():
    # CKA study content is Study domain (cert prep), tagged K8s/CKA.
    r = m("CKA Training", "CKA Phase 1 Lab Session")
    assert r["domain"] == "Study"
    assert "CKA" in r["tags"]


# ── OpenBrain ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("subject", ["openbrain", "OpenBrain", "open-brain"])
def test_openbrain_variants(subject):
    r = m(subject, "architecture")
    assert (r["domain"], r["system"]) == ("OpenBrain", "OpenBrain")


def test_project_documentation_to_openbrain():
    r = m("project", "documentation")
    assert r["domain"] == "OpenBrain"
    assert "ProjectDocs" in r["tags"]


# ── Security ─────────────────────────────────────────────────────────────────

def test_security_subject():
    r = m("security", "security-hardening-2026-05-06")
    assert r["domain"] == "Security"


# ── Study — Annie school subjects ────────────────────────────────────────────

@pytest.mark.parametrize(
    "subject", ["Science", "Biology", "Geometry", "Math", "4th Quarter Science"])
def test_annie_subjects_to_study(subject):
    r = map_to_taxonomy(subject, "test prep", "anneliesepaige", "text", "content")
    assert (r["domain"], r["environment"]) == ("Study", "Study")
    assert r["drop"] is False


# ── Personal / Health / Career ───────────────────────────────────────────────

@pytest.mark.parametrize("subject", ["nutrition", "health", "Health", "food-log"])
def test_food_health_to_personal(subject):
    r = m(subject, "food log")
    assert r["domain"] == "Personal"


def test_career_to_personal():
    r = m("Career", "Interview Stories Bank")
    assert r["domain"] == "Personal"


def test_plain_personal_subject_is_study_env():
    # "Personal" + a non-mental-health topic stays Study environment.
    r = m("Personal", "Mike McMahon history")
    assert (r["domain"], r["environment"]) == ("Personal", "Study")


# ── Curation (b): personal/mental-health -> Personal/Archive ──────────────────

@pytest.mark.parametrize("subject,topic", [
    ("Personal", "Mike McMahon - Core Wound Session 1"),
    ("Personal", "Betrayal"),
    ("Personal", "Betrayal Wound - Session 1"),
    ("Personal - Mental Health", "Betrayal Wound"),
])
def test_mental_health_to_personal_archive(subject, topic):
    r = m(subject, topic)
    assert r["drop"] is False
    assert (r["domain"], r["environment"]) == ("Personal", "Archive")
    assert "Mental-health" in r["tags"]
    assert "Personal" in r["tags"]


# ── Curation (a): drop smoke-test / malformed / garbage ──────────────────────

@pytest.mark.parametrize("subject", [
    "smoke test",
    "live smoke test note",
    "mcp_smoke_test",
    "pdf_smoke_test",
    "docx_url_smoke_test",
    "System",  # subject="System" topic="Test"
])
def test_smoke_test_subjects_dropped(subject):
    r = m(subject, "2026-04-02")
    assert r["drop"] is True
    assert r["domain"] is None


@pytest.mark.parametrize("subject", [
    "[malformed subject: PR smoke check message]",
    "[malformed subject: session summary string]",
    "[malformed subject: RRF smoke check string]",
    "[malformed subject: corrected misconception]",
])
def test_malformed_bracket_subjects_dropped(subject):
    r = m(subject, "ops")
    assert r["drop"] is True
    assert r["domain"] is None


def test_long_sentence_subject_dropped_as_malformed():
    subj = "This is an entire sentence accidentally used as the subject key " \
           "which is far longer than any real subject would ever be in practice"
    r = m(subj, "session work")
    assert r["drop"] is True


def test_smoke_test_owner_dropped_regardless_of_subject():
    r = map_to_taxonomy("live smoke test note", "2026-03-30", "tenant-a-owner", "text", "x")
    assert r["drop"] is True
    assert "tenant-a-owner" in r["reason"]


# ── Null / unknown subjects ──────────────────────────────────────────────────

def test_null_subject_defaults_to_study():
    r = map_to_taxonomy(None, None, None, "slack", "a slack message")
    assert r["drop"] is False
    assert (r["domain"], r["environment"]) == ("Study", "Study")


def test_unknown_subject_defaults_to_study_and_flags_review():
    r = m("some brand new subject nobody mapped", "topic")
    assert r["drop"] is False
    assert (r["domain"], r["environment"]) == ("Study", "Study")
    assert "review" in r["reason"]


# ── engineering bucket (path-based at migration; text rule at live ingest) ────

def test_engineering_text_source_rule():
    # At live ingest there is no source_uri -> text-source rule (Network/Production).
    r = m("engineering", "notes")
    assert (r["domain"], r["environment"]) == ("Network", "Production")
    assert r["drop"] is False


# ── shape derivation (ADR-011 decision 4) ────────────────────────────────────

@pytest.mark.parametrize("source_type,expected", [
    ("obsidian", "document"),
    ("pdf", "document"),
    ("docx", "document"),
    ("text", "note"),
    ("slack", "note"),
    ("unknown", "note"),
    (None, "note"),
])
def test_shape_defaults_by_source_type(source_type, expected):
    assert derive_shape(source_type) == expected


def test_shape_override_honoured_when_valid():
    assert derive_shape("text", "card") == "card"
    assert derive_shape("obsidian", "event") == "event"


def test_shape_override_ignored_when_invalid():
    assert derive_shape("text", "garbage") == "note"


def test_shape_flows_through_map_to_taxonomy():
    r = map_to_taxonomy("Science", "prep", "anneliesepaige", "text", "x", shape_override="card")
    assert r["shape"] == "card"
