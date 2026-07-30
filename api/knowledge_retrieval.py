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
from collections.abc import Mapping
from typing import Any

from api._openbrain_api import (
    _LENGTH_PENALTY_THRESHOLD,
    _RRF_K,
    MAX_RESULTS,
    _confidence_label,
    _or_tsquery_fragment,
    _ts_query,
    _vector_param,
    _word_count,
    embedding_request,
    get_db_conn,
)

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


def _has_component_tag(tags: Any) -> bool:
    """True when any tag is a `component:*` key (the living-doc supersession key)."""
    return any(isinstance(t, str) and t.startswith("component:") for t in (tags or []))

# Columns selected from public.knowledge for both candidate queries.
_SELECT_COLS = """
      id,
      content,
      domain,
      environment,
      system,
      tags,
      status
"""


def _build_filter_clause(
    owner: str | None,
    filters: dict | None,
) -> tuple[str, list[Any]]:
    """Build the shared WHERE fragment + params for status/owner/facet filters.

    - status defaults to 'current'; a caller-supplied filters['status'] overrides.
    - owner scopes via knowledge.created_by when provided.
    - domain/environment/system are AND-ed equality facets when present.
    Returns (clause, params) where clause begins with ' AND ...'.
    """
    filters = filters or {}
    clauses: list[str] = []
    params: list[Any] = []

    status = filters.get("status", _DEFAULT_STATUS)
    if status is not None:
        clauses.append("status = %s")
        params.append(status)

    if owner:
        clauses.append("created_by = %s")
        params.append(owner)

    for col in _ALLOWED_FILTER_COLS:
        value = filters.get(col)
        if value:
            clauses.append(f"{col} = %s")
            params.append(value)

    clause = ("".join(f" AND {c}" for c in clauses)) if clauses else ""
    return clause, params


def search_knowledge_vector_candidates(
    query_embedding: list[float],
    owner: str | None,
    max_results: int,
    filters: dict | None = None,
) -> list[dict[str, Any]]:
    """Nearest-neighbour candidates by cosine distance over knowledge.embedding."""
    vector_param = _vector_param(query_embedding)
    filter_clause, filter_params = _build_filter_clause(owner, filters)

    params: list[Any] = [vector_param]
    params.extend(filter_params)
    params.append(vector_param)
    params.append(max_results)

    query_sql = f"""
    SELECT
    {_SELECT_COLS},
      (embedding <=> %s::vector) AS score
    FROM public.knowledge
    WHERE embedding IS NOT NULL
      {filter_clause}
    ORDER BY embedding <=> %s::vector
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
) -> list[dict[str, Any]]:
    """Full-text candidates over knowledge.content.

    ADR-013: uses an OR-ranked, stemmed tsquery (one plainto_tsquery per term,
    OR-combined) so multi-term queries surface partial matches instead of requiring
    ALL terms (the websearch_to_tsquery AND default). Falls back to
    websearch_to_tsquery only when the query yields no usable terms.
    """
    filter_clause, filter_params = _build_filter_clause(owner, filters)

    or_frag, or_params = _or_tsquery_fragment(query)
    if or_frag is not None:
        tsq, tsq_params = or_frag, or_params
    else:
        tsq, tsq_params = "websearch_to_tsquery('english', %s)", [_ts_query(query)]

    query_sql = f"""
    SELECT
    {_SELECT_COLS},
      ts_rank(to_tsvector('english', coalesce(content, '')), {tsq}) AS score
    FROM public.knowledge
    WHERE to_tsvector('english', coalesce(content, '')) @@ {tsq}
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
    return {
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


def retrieve_knowledge(
    query: str,
    n_results: int,
    owner: str | None,
    filters: dict | None = None,
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
    max_results = min(max(max(1, n_results) * 2, _VECTOR_POOL_FLOOR), MAX_RESULTS)

    vector_rows: list[dict[str, Any]] = []
    try:
        embedding = embedding_request(query)
        if embedding is not None:
            vector_rows = search_knowledge_vector_candidates(
                embedding, owner, max_results, filters
            )
    except Exception:
        vector_rows = []

    keyword_rows: list[dict[str, Any]] = []
    try:
        keyword_rows = search_knowledge_keyword_candidates(
            query, owner, max_results, filters
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

    # Length penalty: very short content is down-weighted (same logic/threshold as
    # retrieve_thoughts). NB: this ONLY penalises fragments under the word threshold
    # — long living docs are untouched (multiplier stays 1.0). The penalty factor is
    # surfaced per result so the effect is observable, not assumed.
    for key, row in row_by_key.items():
        wc = _word_count(row.get("content", ""))
        penalty = 1.0
        if wc < _LENGTH_PENALTY_THRESHOLD:
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
            row.get("status") == "current" and _has_component_tag(row.get("tags"))
        )
        boost = _COMPONENT_BOOST if is_current_component else 1.0
        if boost != 1.0:
            rrf_scores[key] *= boost
        signals[key]["component_boost_applied"] = boost

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

    top_score = rrf_scores[ranked_keys[0]] if ranked_keys else 0.0
    second_score = rrf_scores[ranked_keys[1]] if len(ranked_keys) > 1 else None

    final: list[dict[str, Any]] = []
    for i, key in enumerate(ranked_keys):
        row = row_by_key[key]
        score = rrf_scores[key]
        wc = signals[key]["word_count"]
        if i == 0:
            confidence = _confidence_label(top_score, second_score, wc)
        elif i == 1:
            confidence = _confidence_label(
                score,
                rrf_scores[ranked_keys[2]] if len(ranked_keys) > 2 else None,
                wc,
            )
        else:
            confidence = _confidence_label(score, None, wc)
        sig = signals[key]
        sig["final_score"] = score
        final.append(_shape_result(row, score, confidence, sig))

    return final[: max(1, n_results)]
