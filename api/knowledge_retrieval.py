"""Semantic retrieval over public.knowledge (OB2 cutover — Workstream A).

Mirrors the hybrid vector+keyword retrieval of `retrieve_thoughts`
(`api/_openbrain_api.py`) but targets the new `public.knowledge` table and
fixes the legacy fusion bug: Reciprocal Rank Fusion is keyed on the unique
`knowledge.id` (a UUID), NOT on `(file, source)`. The `(file, source)` key
collapses every text row into one bucket; see
`docs/decisions/ADR-011-document-model-and-ingest-integrity.md` decision 1.

Read-only. SELECT only — no INSERT/UPDATE/DELETE/ALTER.

Public contract (Phase-2 integration depends on this — keep stable):
    retrieve_knowledge(query, n_results, owner, filters=None) -> list[dict]
    each dict: {id, text, score, confidence, domain, environment, system, tags, status}
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from api._openbrain_api import (
    _LENGTH_PENALTY_THRESHOLD,
    _RRF_K,
    MAX_RESULTS,
    _confidence_from_signals,
    _or_tsquery_fragment,
    _ts_query,
    _vector_param,
    _word_count,
    embedding_request,
    get_db_conn,
)
from api.chunking import collapse_chunks

# Filter keys callers may supply via `filters` to AND against the semantic search.
# `status` defaults to 'current'; the rest are optional equality facets.
_ALLOWED_FILTER_COLS = ("domain", "environment", "system")
_DEFAULT_STATUS = "current"


def _env_float(name: str, default: float) -> float:
    """Read a float tuning knob from the environment; fall back to default on
    missing/blank/malformed values so a bad env var never breaks retrieval."""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# --- Retrieval tuning knobs (OB2). All default to a no-op so this change ships
# behavior-neutral; each is set from real numbers after the signal harness baseline. ---

# Component boost: authoritative current-state living docs tagged `component:*` get
# their fused RRF score multiplied by this factor so stale event notes stop
# out-ranking them. 1.0 = OFF (no behavior change) until the weight is tuned.
_COMPONENT_BOOST = _env_float("OPENBRAIN_COMPONENT_BOOST", 1.0)

# Vector-similarity suppression floor: drop candidates whose best vector match is
# below this rather than returning the least-bad of several poor results. 0.0 = OFF
# (suppress nothing) until an empirical floor is set from the baseline distribution.
_VECTOR_SUPPRESSION_FLOOR = _env_float("OPENBRAIN_VECTOR_SUPPRESSION_FLOOR", 0.0)

# IVFFlat recall (ADR-015). The knowledge.embedding index is ivfflat(lists=100).
# pgvector's default ivfflat.probes=1 scans a SINGLE list (~8 of 800 rows), so a
# genuinely top-similarity doc in another list is never considered — that, not
# embedding dilution, is why long current-state docs were missing from vector
# results. probes=10 (=sqrt(lists)) restores near-exact recall at negligible cost
# on a table this small. SET LOCAL keeps it transaction-scoped (pooler-safe).
_IVFFLAT_PROBES = int(_env_float("OPENBRAIN_IVFFLAT_PROBES", 10))

# Candidate-pool floor: fuse from at least this many candidates per retriever
# (capped at MAX_RESULTS) instead of just n_results*2. Cheap insurance so a
# borderline high-similarity doc isn't dropped before fusion even sees it.
_VECTOR_POOL_FLOOR = int(_env_float("OPENBRAIN_VECTOR_POOL_FLOOR", 50))

# Recency net (ADR-018 P1). Down-weights OLD current docs so a stale event note sinks
# below fresher truth on the same topic — the recency signal RRF discards. Gated to the
# three domains where the stale-ranking bug was actually measured (allowlist, not
# denylist), and EXEMPTS durable-tagged living docs + any component-keyed row (those must
# surface, not sink — §4). Half-life in days; 0 (default) = OFF, so this ships
# behaviour-neutral. The floor keeps an old doc from vanishing — it sinks, it isn't deleted.
_RECENCY_HALFLIFE_DAYS = _env_float("OPENBRAIN_RECENCY_HALFLIFE_DAYS", 0.0)
_RECENCY_FLOOR = _env_float("OPENBRAIN_RECENCY_FLOOR", 0.25)
_RECENCY_DOMAINS = {"Network", "K8s", "Security"}


def _recency_factor(created_at: Any, now: datetime) -> float:
    """Age decay 0.5**(age_days / halflife), floored at _RECENCY_FLOOR. Returns 1.0 when
    recency is OFF or the row carries no usable created_at (never penalise on missing data)."""
    if _RECENCY_HALFLIFE_DAYS <= 0 or created_at is None:
        return 1.0
    ca = created_at
    if not isinstance(ca, datetime):
        try:
            ca = datetime.fromisoformat(str(ca))
        except (ValueError, TypeError):
            return 1.0
    if ca.tzinfo is None:
        ca = ca.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (now - ca).total_seconds() / 86400.0)
    return max(_RECENCY_FLOOR, 0.5 ** (age_days / _RECENCY_HALFLIFE_DAYS))


def _has_component_tag(tags: Any) -> bool:
    """True when any tag is a `component:*` key (the living-doc supersession key)."""
    return any(isinstance(t, str) and t.startswith("component:") for t in (tags or []))


def _is_component_row(row: Mapping[str, Any]) -> bool:
    """True when the row is a living doc. Reads the `component_key` COLUMN of record
    (ADR-018a item 4), falling back to the `component:*` tag for rows written before the
    column had a writer — so detection stays whole through the P2 transition."""
    if row.get("component_key") is not None:
        return True
    return _has_component_tag(row.get("tags"))

# Physical read table (ADR-017). Default 'knowledge'; set OPENBRAIN_KNOWLEDGE_TABLE
# =knowledge_chunked to serve chunked sections (adds document_id/heading and triggers
# retrieval-side collapse). Resolved at call time so the flag flips without a redeploy.
_CHUNK_TABLE = "knowledge_chunked"


def _knowledge_table() -> str:
    t = (os.getenv("OPENBRAIN_KNOWLEDGE_TABLE") or "knowledge").strip().lower()
    return _CHUNK_TABLE if t in (_CHUNK_TABLE, "chunked") else "knowledge"


def _query_shape(table: str) -> tuple[str, str, str, str, str, str]:
    """Per-table SQL shape: (select_list, from_clause, meta_prefix, owner_col,
    content_col, embedding_col).

    On the chunked store (ADR-018a item 3) `knowledge_chunked` owns ONLY content +
    embedding + chunk identity + the denormalised `created_by`; every mutable metadata
    column (domain/environment/system/tags/status/component_key/created_at) is JOINed
    from the parent `knowledge` row on `document_id`, so it is single-sourced and cannot
    drift. Metadata filters therefore target the parent alias `k.`; owner scoping stays a
    single-table predicate on the chunk (`kc.created_by`), the security-critical filter.
    """
    if table == _CHUNK_TABLE:
        select = (
            "kc.id, kc.content, k.domain, k.environment, k.system, k.tags, k.status, "
            "k.created_at, k.component_key, kc.document_id, kc.chunk_index, kc.heading"
        )
        frm = "public.knowledge_chunked kc JOIN public.knowledge k ON k.id = kc.document_id"
        return select, frm, "k.", "kc.created_by", "kc.content", "kc.embedding"
    select = "id, content, domain, environment, system, tags, status, created_at, component_key"
    return select, "public.knowledge", "", "created_by", "content", "embedding"


def _build_filter_clause(
    owner: str | None,
    filters: dict | None,
    *,
    meta_prefix: str = "",
    owner_col: str = "created_by",
) -> tuple[str, list[Any]]:
    """Build the shared WHERE fragment + params for status/owner/facet filters.

    - status defaults to 'current'; a caller-supplied filters['status'] overrides.
    - owner scopes via `owner_col` when provided.
    - domain/environment/system are AND-ed equality facets when present.
    `meta_prefix` qualifies the metadata columns (status/domain/environment/system) with a
    table alias for the chunked JOIN (e.g. 'k.'); it is '' for the single-table base path.
    Returns (clause, params) where clause begins with ' AND ...'.
    """
    filters = filters or {}
    clauses: list[str] = []
    params: list[Any] = []

    status = filters.get("status", _DEFAULT_STATUS)
    if status is not None:
        clauses.append(f"{meta_prefix}status = %s")
        params.append(status)

    if owner:
        clauses.append(f"{owner_col} = %s")
        params.append(owner)

    for col in _ALLOWED_FILTER_COLS:
        value = filters.get(col)
        if value:
            clauses.append(f"{meta_prefix}{col} = %s")
            params.append(value)

    clause = ("".join(f" AND {c}" for c in clauses)) if clauses else ""
    return clause, params


def search_knowledge_vector_candidates(
    query_embedding: list[float],
    owner: str | None,
    max_results: int,
    filters: dict | None = None,
    table: str | None = None,
) -> list[dict[str, Any]]:
    """Nearest-neighbour candidates by cosine distance over <table>.embedding."""
    table = table or _knowledge_table()
    vector_param = _vector_param(query_embedding)
    select, frm, meta_prefix, owner_col, _content_col, emb_col = _query_shape(table)
    filter_clause, filter_params = _build_filter_clause(
        owner, filters, meta_prefix=meta_prefix, owner_col=owner_col
    )

    params: list[Any] = [vector_param]
    params.extend(filter_params)
    params.append(vector_param)
    params.append(max_results)

    query_sql = f"""
    SELECT
    {select},
      ({emb_col} <=> %s::vector) AS score
    FROM {frm}
    WHERE {emb_col} IS NOT NULL
      {filter_clause}
    ORDER BY {emb_col} <=> %s::vector
    LIMIT %s;
    """

    with get_db_conn() as conn:
        # Raise ivfflat.probes for THIS transaction so the approximate index scans
        # enough lists to actually surface top-similarity rows (see _IVFFLAT_PROBES).
        if _IVFFLAT_PROBES and _IVFFLAT_PROBES > 0:
            conn.execute(f"SET LOCAL ivfflat.probes = {int(_IVFFLAT_PROBES)}")
        rows = conn.execute(query_sql, params).fetchall()
    return [dict(row) for row in rows]


def search_knowledge_keyword_candidates(
    query: str,
    owner: str | None,
    max_results: int,
    filters: dict | None = None,
    table: str | None = None,
) -> list[dict[str, Any]]:
    """Full-text candidates over <table>.content.

    ADR-013: uses an OR-ranked, stemmed tsquery (one plainto_tsquery per term,
    OR-combined) so multi-term queries surface partial matches instead of requiring
    ALL terms (the websearch_to_tsquery AND default). Falls back to
    websearch_to_tsquery only when the query yields no usable terms.
    """
    table = table or _knowledge_table()
    select, frm, meta_prefix, owner_col, content_col, _emb_col = _query_shape(table)
    filter_clause, filter_params = _build_filter_clause(
        owner, filters, meta_prefix=meta_prefix, owner_col=owner_col
    )

    or_frag, or_params = _or_tsquery_fragment(query)
    if or_frag is not None:
        tsq, tsq_params = or_frag, or_params
    else:
        tsq, tsq_params = "websearch_to_tsquery('english', %s)", [_ts_query(query)]

    query_sql = f"""
    SELECT
    {select},
      ts_rank(to_tsvector('english', coalesce({content_col}, '')), {tsq}) AS score
    FROM {frm}
    WHERE to_tsvector('english', coalesce({content_col}, '')) @@ {tsq}
      {filter_clause}
    ORDER BY score DESC NULLS LAST
    LIMIT %s;
    """

    # Param order matches placeholder order: tsq (SELECT ts_rank), tsq (WHERE @@),
    # filter_params (WHERE facets), max_results. tsq is bound twice.
    params = [*tsq_params, *tsq_params, *filter_params, max_results]

    with get_db_conn() as conn:
        rows = conn.execute(query_sql, params).fetchall()
    return [dict(row) for row in rows]


def _shape_result(
    row: Mapping[str, Any],
    score: float,
    confidence: str,
    signals: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shaped = {
        "id": str(row.get("id")),
        "text": row.get("content", ""),
        "score": score,
        "confidence": confidence,
        "domain": row.get("domain"),
        "environment": row.get("environment"),
        "system": row.get("system"),
        "tags": list(row.get("tags") or []),
        "status": row.get("status"),
        # Retrieval instrument (ADR-014): raw per-retriever signals behind the fused
        # ordinal `score`, so callers can measure relevance instead of reading a
        # constant reciprocal. Additive — existing keys are unchanged.
        "signals": signals or {},
    }
    # Chunk identity (ADR-017) when reading the chunked store — drives collapse.
    if row.get("document_id") is not None:
        shaped["document_id"] = str(row.get("document_id"))
        shaped["chunk_index"] = row.get("chunk_index")
        shaped["heading"] = row.get("heading")
    return shaped


def retrieve_knowledge(
    query: str,
    n_results: int,
    owner: str | None,
    filters: dict | None = None,
    table: str | None = None,
) -> list[dict[str, Any]]:
    """Hybrid (vector + keyword) retrieval over public.knowledge.

    RRF fusion is keyed on the unique `knowledge.id` so distinct rows never
    collapse into a single result (the legacy `thoughts` (file, source) bug).
    Applies the same length-penalty and confidence-label logic as
    `retrieve_thoughts`.

    Args:
        query:     natural-language query string.
        n_results: desired number of results.
        owner:     scopes to knowledge.created_by when truthy; None = no scope.
        filters:   optional dict. status defaults to 'current' (pass
                   {'status': ...} to override, or None to disable). Optional
                   equality facets: domain, environment, system. All AND-ed.

    Returns:
        list of dicts with keys:
        {id, text, score, confidence, domain, environment, system, tags, status}
    """
    table = table or _knowledge_table()
    max_results = min(max(max(1, n_results) * 2, _VECTOR_POOL_FLOOR), MAX_RESULTS)

    vector_rows: list[dict[str, Any]] = []
    try:
        embedding = embedding_request(query)
        if embedding is not None:
            vector_rows = search_knowledge_vector_candidates(
                embedding, owner, max_results, filters, table
            )
    except Exception:
        vector_rows = []

    keyword_rows: list[dict[str, Any]] = []
    try:
        keyword_rows = search_knowledge_keyword_candidates(
            query, owner, max_results, filters, table
        )
    except Exception:
        keyword_rows = []

    if not keyword_rows and not vector_rows:
        return []

    # --- Reciprocal Rank Fusion, keyed on the UNIQUE knowledge.id ---
    # Keying on id (not (file, source)) is the bug-free contract from ADR-011:
    # distinct knowledge rows accumulate independent RRF buckets and never merge.
    #
    # Alongside the fused ordinal we retain each retriever's RAW signal per key
    # (vector cosine distance, lexical ts_rank, and the two ranks). RRF fuses on
    # rank alone and discards magnitude, so the fused score is a fixed reciprocal
    # per position and carries no relevance information — these raw signals are the
    # only relevance instrument the caller gets (ADR-014).
    rrf_scores: dict[Any, float] = {}
    row_by_key: dict[Any, dict[str, Any]] = {}
    signals: dict[Any, dict[str, Any]] = {}

    for rank, row in enumerate(keyword_rows):
        key = row.get("id")
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
        row_by_key[key] = row
        sig = signals.setdefault(key, {})
        sig["lexical_rank"] = rank
        lex = row.get("score")
        sig["lexical_score"] = float(lex) if lex is not None else None

    for rank, row in enumerate(vector_rows):
        key = row.get("id")
        rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank + 1)
        if key not in row_by_key:
            row_by_key[key] = row
        sig = signals.setdefault(key, {})
        sig["vector_rank"] = rank
        # pgvector `<=>` is cosine DISTANCE (0=identical). similarity = 1 - distance.
        dist = row.get("score")
        if dist is not None:
            dist = float(dist)
            sig["vector_distance"] = dist
            sig["vector_similarity"] = 1.0 - dist
        else:
            sig["vector_distance"] = None
            sig["vector_similarity"] = None

    # Normalise every signal bucket and record fused-pre-penalty + retriever coverage.
    for key, sig in signals.items():
        sig.setdefault("lexical_rank", None)
        sig.setdefault("lexical_score", None)
        sig.setdefault("vector_rank", None)
        sig.setdefault("vector_distance", None)
        sig.setdefault("vector_similarity", None)
        sig["retrievers_hit"] = (
            (1 if sig["lexical_rank"] is not None else 0)
            + (1 if sig["vector_rank"] is not None else 0)
        )
        sig["rrf_score"] = rrf_scores[key]

    # Length penalty (ADR-002): very short content is down-weighted as probable noise.
    # NB: this ONLY penalises fragments under the word threshold — long docs are untouched
    # (multiplier stays 1.0). The penalty factor is surfaced per result so the effect is
    # observable, not assumed.
    #
    # It is SKIPPED on the chunked store: the length penalty was designed for whole docs,
    # where short = probable noise. Chunking (ADR-017) intentionally produces short focused
    # sections and already merges sub-minimum fragments (_MIN_CHUNK_WORDS), so docking a
    # 27-word section penalises exactly what the chunker is built to make — the penalty
    # fought the chunker (Chat's eval, 2026-07-30). word_count is still recorded (confidence
    # uses it); only the score multiplication is skipped.
    penalise_short = table != _CHUNK_TABLE
    for key, row in row_by_key.items():
        wc = _word_count(row.get("content", ""))
        penalty = 1.0
        if penalise_short and wc < _LENGTH_PENALTY_THRESHOLD:
            penalty = wc / _LENGTH_PENALTY_THRESHOLD  # 0.0–1.0
            rrf_scores[key] *= penalty
        signals[key]["word_count"] = wc
        signals[key]["length_penalty_applied"] = penalty

    # Component boost: authoritative current-state living docs (`component:*` tag)
    # get their fused score lifted so stale event notes stop out-ranking them.
    # CRITICAL: only status='current' rows are boosted. A superseded/historical row
    # carries the same component:* tag, so an ungated boost would lift stale versions
    # above the live one — the exact disease this is meant to cure. (Caught by the
    # signal harness boost sweep, 2026-07-30.) _COMPONENT_BOOST defaults to 1.0 (OFF)
    # until tuned against that harness.
    for key, row in row_by_key.items():
        is_current_component = (
            row.get("status") == "current" and _is_component_row(row)
        )
        boost = _COMPONENT_BOOST if is_current_component else 1.0
        if boost != 1.0:
            rrf_scores[key] *= boost
        signals[key]["component_boost_applied"] = boost

    # Recency net (ADR-018 P1): down-weight OLD current docs in the measured-failure
    # domains so a stale note sinks below fresher truth. EXEMPT: durable-tagged living
    # docs and any component-keyed row (must surface, §4), and every non-allowlist domain
    # (Study/Personal/OpenBrain untouched — protects Annie's tutor). created_at is the
    # doc's age (chunk created_at mirrors parent, verified). OFF unless the half-life env
    # is set, so this is behaviour-neutral until deliberately enabled.
    if _RECENCY_HALFLIFE_DAYS > 0:
        now = datetime.now(timezone.utc)
        for key, row in row_by_key.items():
            tags = row.get("tags") or []
            exempt = (
                row.get("domain") not in _RECENCY_DOMAINS
                or "durable" in tags
                or _is_component_row(row)
            )
            factor = 1.0 if exempt else _recency_factor(row.get("created_at"), now)
            if factor != 1.0:
                rrf_scores[key] *= factor
            signals[key]["recency_decay_applied"] = factor

    ranked_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)

    # Vector-similarity suppression floor: drop candidates whose best vector match is
    # below the floor rather than returning the least-bad. Keyword-only hits (no
    # vector similarity) are kept — a lexical exact match is still meaningful.
    # _VECTOR_SUPPRESSION_FLOOR defaults to 0.0 (OFF) until set from the baseline.
    if _VECTOR_SUPPRESSION_FLOOR > 0.0:
        kept: list[Any] = []
        for key in ranked_keys:
            vs = signals[key].get("vector_similarity")
            if vs is not None and vs < _VECTOR_SUPPRESSION_FLOOR:
                signals[key]["suppressed"] = True
                continue
            kept.append(key)
        ranked_keys = kept

    ranked_keys = ranked_keys[:max_results]

    second_score = rrf_scores[ranked_keys[1]] if len(ranked_keys) > 1 else None

    final: list[dict[str, Any]] = []
    for i, key in enumerate(ranked_keys):
        row = row_by_key[key]
        score = rrf_scores[key]
        sig = signals[key]
        wc = sig["word_count"]
        # Confidence keys off cosine vector_similarity (boost-independent), with the
        # fused score + neighbour retained for the keyword-only fallback (no similarity).
        vsim = sig.get("vector_similarity")
        # Rank agreement = top of BOTH retriever lists. Scale-free, so it promotes a
        # short-but-bullseye chunk to "high" that the whole-doc word-count floor would
        # otherwise cap at "medium" (surfaced by Chat's eval, 2026-07-30).
        rank_agreement = sig.get("lexical_rank") == 0 and sig.get("vector_rank") == 0
        if i == 0:
            neighbour = second_score
        elif i == 1:
            neighbour = rrf_scores[ranked_keys[2]] if len(ranked_keys) > 2 else None
        else:
            neighbour = None
        confidence = _confidence_from_signals(
            vsim, score, neighbour, wc, rank_agreement, chunked=(table == _CHUNK_TABLE)
        )
        sig["final_score"] = score
        final.append(_shape_result(row, score, confidence, sig))

    # Chunked store (ADR-017): collapse chunk rows to one per parent document so a
    # multi-section living doc presents once, not N times. No-op on `knowledge` (rows
    # have no document_id). Collapse BEFORE truncating to n_results.
    if table == _CHUNK_TABLE:
        final = collapse_chunks(final)

    return final[: max(1, n_results)]


def _shape_fetch(row: Mapping[str, Any]) -> dict[str, Any]:
    """Full-note payload for stage-2 fetch — same provenance synthesis as the
    query adapter so a fetched note looks identical to one surfaced by query. When
    the row is a chunk (ADR-017) it carries document_id/chunk_index/heading so the
    caller can tell which section it got and reassemble siblings."""
    domain = row.get("domain")
    system = row.get("system")
    provenance = "/".join(p for p in (domain, system) if p) or "knowledge"
    created = row.get("created_at")
    out = {
        "id": str(row.get("id")),
        "text": row.get("content", ""),
        "domain": domain,
        "environment": row.get("environment"),
        "system": system,
        "status": row.get("status"),
        "tags": list(row.get("tags") or []),
        "source": provenance,
        "section": row.get("heading") or provenance,
        "created_at": str(created) if created is not None else None,
    }
    if row.get("document_id") is not None:
        out["document_id"] = str(row.get("document_id"))
        out["chunk_index"] = row.get("chunk_index")
        out["heading"] = row.get("heading")
    return out


def fetch_knowledge_by_ids(
    ids: list[Any],
    owner: str | None,
    table: str | None = None,
) -> list[dict[str, Any]]:
    """Stage-2 fetch (ADR-016/017): return FULL text for specific rows, OWNER-SCOPED.
    `created_by` must equal the caller's authenticated owner, so a caller can never
    fetch another tenant's row by id. Non-UUID and foreign/unknown ids are silently
    dropped (returns [] rather than raising). Read-only, SELECT only.

    On the chunked store an id may be a CHUNK id (returns that one section) OR a
    document_id (returns all sections of that document, ordered) — so skim→fetch works
    whether the agent asks for the section it picked or the whole living doc."""
    table = table or _knowledge_table()
    clean: list[str] = []
    for i in ids or []:
        try:
            clean.append(str(uuid.UUID(str(i))))
        except (ValueError, AttributeError, TypeError):
            continue
    if not clean:
        return []
    # Owner scoping is mandatory for cross-tenant isolation; the caller passes the
    # AUTHENTICATED owner (never client-supplied). A falsy owner scopes to nothing.
    if not owner:
        return []

    if table == _CHUNK_TABLE:
        # Metadata JOINed from the parent (ADR-018a item 3); chunk owns only content +
        # identity + the denormalised created_by used for owner-scoping.
        sql = """
        SELECT kc.id, kc.content, kc.document_id, kc.chunk_index, kc.heading,
               k.domain, k.environment, k.system, k.tags, k.status, k.created_at
        FROM public.knowledge_chunked kc
        JOIN public.knowledge k ON k.id = kc.document_id
        WHERE (kc.id = ANY(%s::uuid[]) OR kc.document_id = ANY(%s::uuid[]))
          AND kc.created_by = %s
        ORDER BY kc.document_id, kc.chunk_index
        """
        params: list[Any] = [clean, clean, owner]
    else:
        sql = """
        SELECT id, content, domain, environment, system, tags, status, created_at
        FROM public.knowledge
        WHERE id = ANY(%s::uuid[]) AND created_by = %s
        """
        params = [clean, owner]

    with get_db_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_shape_fetch(dict(row)) for row in rows]
