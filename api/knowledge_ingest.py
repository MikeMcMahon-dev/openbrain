"""Ingest path into ``public.knowledge`` with server-side field integrity (OB2
Workstream B). Mirrors ``_write_text_ingest`` (api/_openbrain_api.py) but targets
the OB2 ``knowledge`` table with validated taxonomy + a content ``shape`` signal.

This is the low-level writer. The taxonomy is derived by ``api.taxonomy_map`` and
passed in here; this module owns *field integrity* (reject empty/invalid input,
never substitute junk) and the deterministic idempotency key (``ingest_id``).

Contract (from OB2-CUTOVER-PLAN.md, mirrors api/ob2_state.handle_ingest_state):
    write_knowledge(content, owner, *, domain, environment, system=None,
                    tags=None, shape=None, status='current',
                    source='cutover:ingest') -> dict

Returns a result dict with a top-level ``status`` field:
  - {"status": "accepted", "id", "ingest_id", "owner", "domain", "environment",
     "shape"}                                  on success
  - {"status": "rejected", "code": 400, "error", "message"}   on integrity failure
  - {"status": "conflict", "code": 409, "error", "message", "details"}
        when the duplicate-current trigger fires (do NOT crash; surface the
        supersession path)
  - {"status": "error", "code": 500, "error", "message"}      on other DB failure

Note: ``shape`` has no first-class column in ``public.knowledge`` yet (ADR-011
decision 4 stores it in metadata JSONB / as a tag until promoted). The knowledge
table has no metadata column, so we persist the shape as a ``shape:<value>`` tag
so the signal is queryable via the GIN tags index without a migration. The
returned dict still reports ``shape`` separately for callers that want it raw.
"""

from __future__ import annotations

import hashlib
from typing import Any

from api._openbrain_api import (
    _vector_param,
    embedding_request,
    get_db_conn,
)
from api.taxonomy_map import (
    VALID_DOMAINS,
    VALID_ENVIRONMENTS,
    VALID_SHAPES,
    _tag_key,
    derive_shape,
    normalize_tags,
)

VALID_STATUSES = {"current", "superseded", "historical", "draft"}

# Max length of the sample_context excerpt stored on a tag_proposals row (review aid).
_PROPOSAL_SAMPLE_LEN = 200


def _load_tag_vocabulary(conn) -> set[str] | None:
    """Return the DB-backed controlled vocabulary (the runtime source of truth).

    Returns None if public.tag_vocabulary is unreachable (e.g. migration 003 not
    applied), signalling callers to fall back to the static CANONICAL_TAGS seed via
    normalize_tags(allowed=None). Never raises — vocabulary lookup is best-effort.

    Wrapped in a SAVEPOINT so a failed SELECT (missing table) does not abort the
    surrounding transaction and break the knowledge INSERT that follows.
    """
    try:
        with conn.transaction():
            rows = conn.execute("SELECT tag FROM public.tag_vocabulary").fetchall()
    except Exception:
        return None
    return {r["tag"] for r in rows}


def _record_tag_proposals(
    conn,
    unknown: list[str],
    owner: str,
    content: str,
) -> None:
    """UPSERT unknown tags into public.tag_proposals (ADR-012 review queue).

    Fail-soft: if the table does not exist yet (migration 004 not applied) or any
    write errors, we swallow it so ingest is never broken by the governance queue.
    On conflict we bump occurrences, refresh last_seen, and update the raw/sample so
    the most recent sighting is what a reviewer sees. Casing/spacing variants collapse
    onto one row via the _tag_key-normalized primary key.
    """
    if not unknown:
        return
    sample = (content or "").strip()[:_PROPOSAL_SAMPLE_LEN] or None
    # Wrap in a SAVEPOINT so a failure here (e.g. tag_proposals not yet created by
    # migration 004) does not poison the surrounding transaction and abort the
    # knowledge INSERT that follows. The savepoint rolls back; the outer tx survives.
    try:
        with conn.transaction():
            # Dedup variants within this call so we don't double-insert the same key.
            seen: set[str] = set()
            for raw in unknown:
                key = _tag_key(raw)
                if not key or key in seen:
                    continue
                seen.add(key)
                conn.execute(
                    """
                    INSERT INTO public.tag_proposals
                        (normalized_key, raw_tag, occurrences, sample_owner, sample_context)
                    VALUES (%s, %s, 1, %s, %s)
                    ON CONFLICT (normalized_key) DO UPDATE SET
                        occurrences    = public.tag_proposals.occurrences + 1,
                        last_seen      = now(),
                        raw_tag        = EXCLUDED.raw_tag,
                        sample_owner   = EXCLUDED.sample_owner,
                        sample_context = EXCLUDED.sample_context
                    """,
                    [key, raw, owner or None, sample],
                )
    except Exception:
        # tag_proposals missing or write failed — do not break ingest.
        return


def compute_knowledge_ingest_id(
    content: str,
    owner: str,
    domain: str,
    environment: str,
    system: str | None,
) -> str:
    """Deterministic idempotency key for a knowledge row.

    Mirrors the spirit of compute_ingest_id / _write_text_ingest's hashing: the
    same content+owner+taxonomy re-ingested yields the same ingest_id, so a
    migration or retry is traceable/idempotent at the provenance layer.
    """
    content_hash = hashlib.sha256((content or "").encode("utf-8")).hexdigest()
    raw = f"{owner}|{domain}|{environment}|{system or ''}|{content_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _reject(message: str) -> dict[str, Any]:
    return {"status": "rejected", "code": 400, "error": "bad_request", "message": message}


def write_knowledge(
    content: str,
    owner: str,
    *,
    domain: str,
    environment: str,
    system: str | None = None,
    tags: list[str] | None = None,
    shape: str | None = None,
    status: str = "current",
    source: str = "cutover:ingest",
    source_type: str | None = None,
    supersedes_id: str | None = None,
    auto_supersede: bool = True,
) -> dict[str, Any]:
    """Embed ``content`` and INSERT one row into ``public.knowledge``.

    Field integrity (ADR-011 decision 3): reject empty content/owner and require
    a valid domain + environment. Never silently substitute junk — a bad taxonomy
    is a hard 400, not a default.

    Living-doc auto-supersession (ADR-008): a note carrying a ``component:*`` tag
    declares itself the canonical record for ``(system, component)``. When
    ``auto_supersede`` is on (default), a new ``current`` write for an identity that
    already has a ``current`` row atomically flips the prior row(s) to
    ``superseded`` (stamping ``valid_until``) and links the new row via
    ``supersedes_id`` — so an "overwrite" replaces in place instead of piling up a
    second ``current`` competitor. Notes WITHOUT a ``component:*`` tag are
    append-only event/history and never supersede anything. This branch is a no-op
    unless a ``component:*`` tag + non-null ``system`` are present, so append-only
    callers are unaffected.

    Handles the duplicate-current trigger by returning a 409-style dict instead
    of crashing (matches handle_ingest_state). Study content (system IS NULL) is
    exempt from the trigger by design, so it never 409s.
    """
    content = (content or "").strip()
    owner = (owner or "").strip()
    domain = (domain or "").strip()
    environment = (environment or "").strip()

    # ── Field integrity ──────────────────────────────────────────────────────
    if not content:
        return _reject("content is required")
    if not owner:
        return _reject("owner is required")
    if not domain:
        return _reject("domain is required")
    if not environment:
        return _reject("environment is required")
    if domain not in VALID_DOMAINS:
        return _reject(f"domain must be one of {sorted(VALID_DOMAINS)}")
    if environment not in VALID_ENVIRONMENTS:
        return _reject(f"environment must be one of {sorted(VALID_ENVIRONMENTS)}")

    status = (status or "current").strip()
    if status not in VALID_STATUSES:
        return _reject(f"status must be one of {sorted(VALID_STATUSES)}")

    system = (system.strip() if isinstance(system, str) else system) or None

    # Clean the incoming tags to a list[str]; canonicalization (folding to the
    # controlled vocabulary) happens inside the DB block below, where the DB-backed
    # tag_vocabulary can be consulted as the runtime source of truth (ADR-012).
    raw_tags: list[str] = []
    if isinstance(tags, list):
        raw_tags = [str(t).strip() for t in tags if str(t).strip()]
    resolved_shape = derive_shape(source_type, shape)
    if resolved_shape not in VALID_SHAPES:  # defensive — derive_shape always returns valid
        resolved_shape = "note"
    shape_tag = f"shape:{resolved_shape}"

    supersedes_id = (supersedes_id.strip() if isinstance(supersedes_id, str) else None) or None

    ingest_id = compute_knowledge_ingest_id(content, owner, domain, environment, system)

    embedding = None
    try:
        embedding = embedding_request(content)
    except Exception:
        embedding = None  # embedding is best-effort, same as handle_ingest_state

    try:
        with get_db_conn() as conn:
            # ── Tag governance (ADR-012) ──────────────────────────────────────
            # Fold incoming tags to the controlled vocabulary, preferring the
            # DB-backed tag_vocabulary (honours tags approved via tag_review.py
            # without a code deploy); fall back to the static CANONICAL_TAGS seed
            # if that table is unreachable. UNKNOWN tags are queued as proposals
            # for human review and NEVER written to knowledge.tags or dropped
            # silently. The shape:<value> signal tag is appended AFTER folding so
            # it bypasses the vocabulary (it is a structural marker, not a facet).
            allowed = _load_tag_vocabulary(conn)
            canonical_tags, unknown_tags = normalize_tags(raw_tags, allowed=allowed)
            _record_tag_proposals(conn, unknown_tags, owner, content)
            tag_list = list(canonical_tags)
            if shape_tag not in tag_list:
                tag_list.append(shape_tag)

            # ── Living-doc auto-supersession (ADR-008) ────────────────────────
            # If this is a `current` write for a keyed living doc (has a
            # component:* identity tag + a system) and the caller did not pin a
            # supersedes_id, retire the prior current row(s) for that exact
            # (system, component) in the SAME transaction and link this row to
            # the most-recent one. Same atomicity as the INSERT below, so a
            # failure rolls back both. No component tag => append-only => skipped.
            if auto_supersede and status == "current" and system and not supersedes_id:
                component_tag = next(
                    (t for t in tag_list if t.startswith("component:")), None
                )
                if component_tag is not None:
                    prior = conn.execute(
                        """
                        SELECT id FROM public.knowledge
                        WHERE system = %s AND status = 'current' AND tags @> ARRAY[%s]
                        ORDER BY valid_from DESC
                        """,
                        [system, component_tag],
                    ).fetchall()
                    if prior:
                        supersedes_id = str(prior[0]["id"])
                        conn.execute(
                            """
                            UPDATE public.knowledge
                            SET status = 'superseded', valid_until = now()
                            WHERE system = %s AND status = 'current' AND tags @> ARRAY[%s]
                            """,
                            [system, component_tag],
                        )

            embedding_val = _vector_param(embedding) if embedding else None
            row = conn.execute(
                """
                INSERT INTO public.knowledge
                    (content, embedding, domain, environment, system, tags,
                     status, supersedes_id, ingest_id, source, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                [content, embedding_val, domain, environment, system, tag_list,
                 status, supersedes_id, ingest_id, source, owner],
            ).fetchone()
            new_id = str(row["id"]) if row else None
    except Exception as exc:
        msg = str(exc)
        _low = msg.lower()
        if "duplicate current record" in _low or "cannot create duplicate" in _low:
            return {
                "status": "conflict",
                "code": 409,
                "error": "conflict",
                "message": (
                    "A current record already exists for this system/tags combination. "
                    "Use the supersession path (POST /api/propose_supersession) to replace it."
                ),
                "details": msg,
            }
        return {"status": "error", "code": 500, "error": "db_error", "message": msg}

    return {
        "status": "accepted",
        "code": 200,
        "id": new_id,
        "ingest_id": ingest_id,
        "owner": owner,
        "domain": domain,
        "environment": environment,
        "shape": resolved_shape,
        "tags": tag_list,
        # Non-null when living-doc auto-supersession retired a prior current row.
        "superseded_id": supersedes_id,
    }
