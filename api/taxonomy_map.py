"""Server-side taxonomy mapper for the OB2 cutover (Workstream B).

Family GPT ingest paths send ``subject``/``topic``/``owner`` in ``thoughts``
metadata plus content — they never send ``domain``/``environment``. This module
derives the OB2 ``knowledge`` taxonomy from those legacy signals, encoding the
evidence in ``docs/migrations/001_domain_discovery.md`` (the actual subject ->
domain mapping for the 699 rows).

It is a pure function: no DB, no network. ``write_knowledge`` (in
``api/knowledge_ingest.py``) consumes the result and enforces field integrity.

Curation decisions from the owner (see OB2-CUTOVER-PLAN.md "Open decisions"):
  (a) smoke-test / malformed / garbage subjects are DROPPED — the mapper returns
      ``drop=True`` and never invents a domain.
  (b) personal/mental-health content ("Core Wound", "Betrayal", ...) maps to
      ``domain=Personal, environment=Archive``.

Output contract:
    map_to_taxonomy(subject, topic, owner, source_type, content) -> dict with keys
      {domain, environment, system, tags, shape, drop: bool, reason}

When ``drop`` is True the taxonomy fields are still populated with safe nulls so
callers can log without KeyErrors, but ``domain``/``environment`` are ``None`` —
the mapper never fabricates a taxonomy for a dropped row.
"""

from __future__ import annotations

import re
from typing import Any

# Authoritative enums — must match api/ob2_state.py VALID_DOMAINS / VALID_ENVIRONMENTS
# and the public.knowledge CHECK constraints (docs/OB2-DESIGN.md).
VALID_DOMAINS = {"Network", "K8s", "Security", "Study", "OpenBrain", "Personal"}
VALID_ENVIRONMENTS = {"Production", "Lab", "Study", "Archive"}
VALID_SHAPES = {"card", "document", "note", "event"}

# shape default by source_type (ADR-011 decision 4):
#   obsidian | pdf | docx -> document ; text | slack -> note
_SHAPE_BY_SOURCE_TYPE = {
    "obsidian": "document",
    "pdf": "document",
    "docx": "document",
    "url": "document",
    "project_docs": "document",
    "text": "note",
    "slack": "note",
}
_DEFAULT_SHAPE = "note"

# ── Controlled tag vocabulary (taxonomy governance, ADR-012) ────────────────────────
# The SEED lives in api/canonical_tags.py (a flat, machine-editable list). The DB table
# public.tag_vocabulary is the runtime source of truth (extended via tag_review.py);
# this seed bootstraps it and is the offline fallback. Producers propose; normalize_tags
# disposes. Closed-by-default: unknown tags are returned for review, never silently kept.
from api.canonical_tags import CANONICAL_TAGS as _SEED_TAGS  # noqa: E402

CANONICAL_TAGS: frozenset[str] = frozenset(_SEED_TAGS)

# Variant key -> canonical. Keys are _tag_key-normalized (lowercase, -/_ /space folded).
# A canonical value of None means DROP (the tag is retired, e.g. smoke-test scaffolding).
TAG_ALIASES: dict[str, str | None] = {
    "sessions": "Session",
    "mentalhealth": "Mental-health",
    "interview-prep": "Interview",
    "interviewprep": "Interview",
    "kubernetes": "K8s",
    "rhel": "RedHat",
    "red-hat": "RedHat",
    "infrastructure-as-code": "IaC",
    "baremetal": "Bare-Metal",
    "nvidia-prep": "NV-Prep",
    "nv-prep": "NV-Prep",
    "smoketest": None,   # retired: smoke-test scaffolding
    "smoke-test": None,
}


def _tag_key(tag: str | None) -> str:
    return (tag or "").strip().lower().replace(" ", "-").replace("_", "-")


# Casing-insensitive index of canonical tags (so "iac"->"IaC", "homelab"->"Homelab"
# fold automatically without an explicit alias). Explicit aliases override this.
_CANON_BY_KEY: dict[str, str] = {_tag_key(t): t for t in CANONICAL_TAGS}


def normalize_tags(
    tags: list[str] | None,
    allowed: "frozenset[str] | set[str] | None" = None,
) -> tuple[list[str], list[str]]:
    """Fold incoming tags to the canonical vocabulary.

    Returns (canonical, unknown). Canonical is de-duped, order-preserved, and only
    contains allowed tags. Explicit-drop aliases (value None) are removed. Unknown tags
    are returned for the caller to queue for review (ADR-012 approval flow) — never
    silently kept and never silently lost.

    `allowed` lets the live ingest path pass the DB-backed `tag_vocabulary` (the runtime
    source of truth, extended via scripts/tag_review.py approvals) so newly-approved tags
    are honoured without a code deploy. Defaults to the static CANONICAL_TAGS seed.
    """
    canon_by_key = _CANON_BY_KEY if allowed is None else {_tag_key(t): t for t in allowed}
    canonical: list[str] = []
    unknown: list[str] = []
    for tag in tags or []:
        key = _tag_key(tag)
        if not key:
            continue
        if ":" in key:
            # Namespaced functional tags (shape:<v> from ADR-011, component:<id> from
            # ADR-008 dedup) are NOT descriptive vocabulary — pass through unchanged,
            # never flag as unknown, never queue for approval.
            if tag not in canonical:
                canonical.append(tag)
            continue
        if key in TAG_ALIASES:
            canon = TAG_ALIASES[key]
            if canon is None:
                continue  # explicit drop
            # an aliased target must still be allowed (it normally is)
            if _tag_key(canon) not in canon_by_key:
                unknown.append(tag)
                continue
        else:
            canon = canon_by_key.get(key)
        if canon is None:
            unknown.append(tag)
            continue
        if canon not in canonical:
            canonical.append(canon)
    return canonical, unknown

# Subjects that are pure test/garbage artifacts — DROP, never migrate (curation (a)).
_DROP_SUBJECTS = {
    "live smoke test note",
    "smoke test",
    "mcp_smoke",
    "mcp_smoke_test",
    "pdf_smoke_test",
    "docx_url_smoke_test",
    "system",  # subject="System" topic="Test" — a test artifact row
}

# Owners that only ever produced smoke-test artifacts -> drop regardless of subject.
_DROP_OWNERS = {"tenant-a-owner"}

# Personal / mental-health topics that must land in Personal/Archive (curation (b)).
# Matched case-insensitively as substrings against subject OR topic.
_PERSONAL_ARCHIVE_MARKERS = (
    "core wound",
    "betrayal",
    "mental health",
)

# Flat subject -> taxonomy rules, transcribed from the signed-off mapping block in
# 001_domain_discovery.md. Keys are matched case-insensitively (the corpus mixes
# "openbrain"/"OpenBrain", "mike"/"Mike", "kubernetes"/"Kubernetes", etc.).
# Each value: (domain, environment, system, tags).
_SUBJECT_RULES: dict[str, tuple[str, str, str | None, list[str]]] = {
    # ── Network / Production state ──
    "home network": ("Network", "Production", "SpectreNet", ["Network", "Production"]),
    "spectrenet": ("Network", "Production", "SpectreNet", ["Network", "Production"]),
    "homelab": ("Network", "Production", "PMX-01", ["Network", "Production", "Proxmox"]),
    # ── Kubernetes ──
    "kubernetes": ("K8s", "Lab", None, ["K8s", "Lab"]),
    "cka training": ("Study", "Study", None, ["K8s", "CKA"]),
    "cka-study": ("Study", "Study", None, ["K8s", "CKA"]),
    # ── OpenBrain system ──
    "open-brain": ("OpenBrain", "Study", "OpenBrain", ["OpenBrain"]),
    "openbrain": ("OpenBrain", "Study", "OpenBrain", ["OpenBrain"]),
    "architecture": ("OpenBrain", "Study", "OpenBrain", ["OpenBrain", "Architecture"]),
    "session-context": ("OpenBrain", "Study", "OpenBrain", ["OpenBrain", "Session"]),
    # ── Project / agent-lab projects ──
    "project": ("OpenBrain", "Study", None, ["ProjectDocs"]),
    "agent-lab": ("OpenBrain", "Study", None, ["AgentLab"]),
    "multi-agent-lab": ("OpenBrain", "Study", None, ["MultiAgentLab"]),
    "home-lab": ("Study", "Study", None, ["HomeLab", "Reference"]),
    "homelab notes": ("Study", "Study", None, ["HomeLab", "Reference"]),
    "git / workflow": ("Study", "Study", None, ["Reference"]),
    # ── Annie's Linux study cards (31) — family-visible Study, system=Annie ──
    "linux command line": ("Study", "Study", "Annie", ["Annie", "Linux"]),
    # ── Personal (default environment Study; mental-health override -> Archive) ──
    "mike": ("Personal", "Study", None, ["Personal", "Mike"]),
    "personal": ("Personal", "Study", None, ["Personal"]),
    "personal - mental health": ("Personal", "Archive", None, ["Personal", "Mental-health"]),
    "beth": ("Personal", "Study", None, ["Personal", "Family"]),
    "annie": ("Personal", "Study", None, ["Personal", "Family"]),
    # ── Career ──
    "career": ("Personal", "Study", None, ["Career"]),
    # ── Study — Annie school subjects ──
    "science": ("Study", "Study", None, ["Science", "Annie"]),
    "biology": ("Study", "Study", None, ["Biology", "Annie"]),
    "geometry": ("Study", "Study", None, ["Geometry", "Annie"]),
    "math": ("Study", "Study", None, ["Math", "Annie"]),
    "4th quarter science": ("Study", "Study", None, ["Science", "Annie"]),
    "learning behavior": ("Study", "Study", None, ["Study", "Annie"]),
    "system preference": ("Study", "Study", None, ["Study", "Annie"]),
    # ── Study — Mike engineering study ──
    "engineering philosophy": ("Study", "Study", None, ["Engineering", "Reference"]),
    "ai engineering": ("Study", "Study", None, ["AI", "Engineering"]),
    "ai development": ("Study", "Study", None, ["AI", "Engineering"]),
    "database security architecture": ("Study", "Study", None, ["Security", "Architecture"]),
    # ── Security ──
    "security": ("Security", "Study", None, ["Security"]),
    # ── Health / Nutrition (all Personal) ──
    "nutrition": ("Personal", "Study", None, ["Personal", "Health", "Nutrition"]),
    "health": ("Personal", "Study", None, ["Personal", "Health"]),
    "food-log": ("Personal", "Study", None, ["Personal", "Health", "FoodLog"]),
    "preferences": ("Personal", "Study", None, ["Personal", "Preferences"]),
    # ── Portfolio blog ──
    "portfolio-blog": ("Study", "Study", None, ["PortfolioBlog"]),
    "claude working sessions": ("Study", "Study", None, ["Sessions"]),
}

# "engineering" + "notes" requires path-based classification (391 rows). The path
# evidence (source_uri prefixes) is not carried on the family GPT ingest call, so
# at *live ingest time* there is no file path — the _text_source_rule applies:
# operational state notes from chat -> Network/Production (per 001_domain_discovery.md).
# Path-based migration of the existing 391 obsidian rows is Workstream C's job, not
# the live ingest path. We expose the text-source rule here for completeness.
_ENGINEERING_TEXT_RULE: tuple[str, str, str | None, list[str]] = (
    "Network", "Production", None, ["Network", "Production"],
)


def _norm(value: str | None) -> str:
    return (value or "").strip()


def _looks_malformed(subject: str) -> bool:
    """Heuristic for sentences-as-subjects / garbage (001_domain_discovery.md)."""
    if subject.startswith("[malformed subject:"):
        return True
    # Subjects > 80 chars are full sentences used as the subject key (smoke/PR messages).
    if len(subject) > 80:
        return True
    # Subjects that are obviously test scaffolding by name.
    low = subject.lower()
    if re.search(r"\bsmoke[_\s-]?test\b", low) or low.endswith("_smoke_test"):
        return True
    return False


def derive_shape(source_type: str | None, override: str | None = None) -> str:
    """Server-side shape (ADR-011 decision 4). Override only honoured if valid."""
    if override:
        ov = override.strip().lower()
        if ov in VALID_SHAPES:
            return ov
    st = _norm(source_type).lower()
    return _SHAPE_BY_SOURCE_TYPE.get(st, _DEFAULT_SHAPE)


def map_to_taxonomy(
    subject: str | None,
    topic: str | None,
    owner: str | None,
    source_type: str | None,
    content: str | None = None,
    source_uri: str | None = None,
    *,
    shape_override: str | None = None,
) -> dict[str, Any]:
    """Map legacy ingest signals to OB2 ``knowledge`` taxonomy.

    Returns a dict: {domain, environment, system, tags, shape, drop, reason, drop_kind}.
    Never invents a domain for a dropped row (domain/environment are None when
    drop=True). Never silently defaults junk: malformed/smoke-test subjects drop.

    ``drop_kind`` is None unless drop=True, and then distinguishes an opt-in no-write
    ("allowlisted": smoke-test owner/subject) from a heuristic one ("malformed"). The
    ingest path fails closed on the latter — see _write_text_ingest_knowledge.
    """
    subj = _norm(subject)
    top = _norm(topic)
    own = _norm(owner)
    shape = derive_shape(source_type, shape_override)

    # Cross-cutting career/interview tags: interview material is scattered across
    # Personal/Study/engineering, so we tag it consistently to retrieve it as a set
    # regardless of domain. Signal = subject/topic markers (content-level re-tagging
    # of already-migrated rows is handled by scripts/retag_knowledge.py).
    _hay = f"{subj} {top}".lower()
    _CAREER_MARKERS = ("interview", "stories bank", "positioning", "ammunition", "star method")
    xtags = ["Career", "Interview"] if any(m in _hay for m in _CAREER_MARKERS) else []

    def result(
        domain: str | None,
        environment: str | None,
        system: str | None,
        tags: list[str],
        *,
        drop: bool,
        reason: str,
        drop_kind: str | None = None,
    ) -> dict[str, Any]:
        # Normalize ALL emitted tags through the controlled vocabulary so the mapper
        # can never re-introduce drift (ADR-012). xtags are added before normalization.
        canonical, _unknown = normalize_tags(list(tags) + (xtags if not drop else []))
        return {
            "domain": domain,
            "environment": environment,
            "system": system,
            "tags": canonical,
            "shape": shape,
            "drop": drop,
            "reason": reason,
            "drop_kind": drop_kind,
        }

    # Not every drop means the same thing to the caller, and the ingest path has to tell them
    # apart (2026-08-25). DROP_ALLOWLISTED is an opt-in "do not write this" — the smoke suite
    # deliberately ingests under these owners/subjects so its probes never reach the vault, and
    # it asserts `accepted`. DROP_MALFORMED is a heuristic firing on a note somebody meant to
    # keep, so it fails closed instead: a real note discarded under a 200/accepted is exactly
    # the silent-loss case that hid eight whitepaper sections. See _write_text_ingest_knowledge.
    DROP_ALLOWLISTED = "allowlisted"
    DROP_MALFORMED = "malformed"

    # 1. Drop: smoke-test artifact owner.
    if own.lower() in _DROP_OWNERS:
        return result(None, None, None, [], drop=True, drop_kind=DROP_ALLOWLISTED,
                      reason=f"drop: smoke-test owner '{own}'")

    # 2. Drop: malformed / sentence-as-subject / garbage.
    if subj and _looks_malformed(subj):
        return result(None, None, None, [], drop=True, drop_kind=DROP_MALFORMED,
                      reason=f"drop: malformed/garbage subject '{subj[:40]}'")

    # 3. Drop: known smoke-test subjects.
    if subj.lower() in _DROP_SUBJECTS:
        return result(None, None, None, [], drop=True, drop_kind=DROP_ALLOWLISTED,
                      reason=f"drop: smoke-test subject '{subj}'")

    # 3b. Annie schooling (assessment / study plans) — family-visible Study, system=Annie
    #     (confirmed not sensitive). Tag by topic: assessment -> Testing; plan -> Ubuntu Study.
    if subj.lower().startswith(("annie —", "annie -")) or "educational assessment" in subj.lower():
        tl = top.lower()
        if any(k in tl for k in ("woodcock", "assessment", "result", "test")):
            extra = "Testing"
        elif any(k in tl for k in ("curriculum", "study plan", "ubuntu", "linux")):
            extra = "Ubuntu Study"
        else:
            extra = "Schooling"
        return result("Study", "Study", "Annie", ["Annie", extra], drop=False,
                      reason=f"Annie schooling -> Study/system=Annie/{extra}")

    # 4. Personal / mental-health override -> Personal/Archive (curation (b)).
    #    Checked before flat rules so "Personal" + "Core Wound" lands in Archive.
    haystack = f"{subj} {top}".lower()
    if any(marker in haystack for marker in _PERSONAL_ARCHIVE_MARKERS):
        return result("Personal", "Archive", None,
                      ["Personal", "Mental-health"], drop=False,
                      reason="personal/mental-health -> Personal/Archive")

    # 5. engineering bucket — path-based at migration; text-source rule at live ingest.
    #    The 391 path-backed obsidian rows were already migrated in Stage 2; the live
    #    ingest path and the current cutover gap carry no source_uri (verified: 0 rows),
    #    so the text-source rule applies. When a path IS present, use its basename as a
    #    system hint; the detailed path->domain table (001_domain_discovery.md) is a
    #    future port and is not needed for any row in flight today.
    if subj.lower() == "engineering":
        domain, environment, system, tags = _ENGINEERING_TEXT_RULE
        uri = _norm(source_uri)
        if uri:
            system = uri.rsplit("/", 1)[-1].rsplit(".", 1)[0] or system
            return result(domain, environment, system, tags, drop=False,
                          reason=f"engineering w/ path -> Network/Production, system={system}")
        return result(domain, environment, system, tags, drop=False,
                      reason="engineering text-source rule (no path) -> Network/Production")

    # 5b. Homelab/SpectreNet infrastructure — split by topic (owner decision 2026-06-17):
    #     k8s-migration topics -> K8s/Production; DNS/topology operational state ->
    #     Network/Production. system stays SpectreNet in both (same infrastructure).
    if subj.lower() in ("homelab infrastructure", "infrastructure"):
        if re.search(r"kubernetes|k8s|\bmigration\b", top.lower()):
            return result("K8s", "Production", "SpectreNet", ["K8s", "Production"],
                          drop=False,
                          reason=f"infra k8s-migration topic '{top[:40]}' -> K8s/Production")
        return result("Network", "Production", "SpectreNet", ["Network", "Production"],
                      drop=False,
                      reason=f"infra DNS/topology topic '{top[:40]}' -> Network/Production")

    # 6. Flat subject rules.
    rule = _SUBJECT_RULES.get(subj.lower())
    if rule is not None:
        domain, environment, system, tags = rule
        return result(domain, environment, system, tags, drop=False,
                      reason=f"subject rule '{subj}'")

    # 7. No subject at all (null/null Slack rows, 23 of them) -> Study/Study default.
    if not subj:
        return result("Study", "Study", None, [], drop=False,
                      reason="null subject -> Study/Study default")

    # 8. Unknown subject — DO NOT invent a confident domain. Default conservatively
    #    to Study/Study (the migration doc's __default__) but flag it for review.
    return result("Study", "Study", None, [], drop=False,
                  reason=f"unknown subject '{subj}' -> Study/Study default (review)")
