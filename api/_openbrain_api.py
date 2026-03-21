from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from psycopg import connect
    from psycopg.rows import dict_row
except Exception:  # pragma: no cover
    connect = None
    dict_row = None

try:
    import sys

    sys.path.append(str(Path(__file__).resolve().parent.parent / "scripts"))
    from tutor import build_tutor_packet
except Exception:  # pragma: no cover
    build_tutor_packet = None


ALLOWED_QUERY_MODES = {"explain", "quiz", "flashcards"}
DEFAULT_OWNER = os.getenv("OPENBRAIN_DEFAULT_OWNER", "mmcmahon")
DEFAULT_TENANT = os.getenv("OPENBRAIN_DEFAULT_TENANT_ID", "family")
DEFAULT_RESULTS = 5
MAX_RESULTS = 50
EMBEDDING_MODEL = os.getenv("OPENBRAIN_EMBEDDING_MODEL", "text-embedding-3-small")
DB_URL = (
    os.getenv("OPENBRAIN_SUPABASE_CONNECTION_STRING")
    or os.getenv("SUPABASE_DB_URL")
    or os.getenv("SUPABASE_DATABASE_URL")
    or os.getenv("DATABASE_URL")
)
EMBEDDING_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
EMBEDDING_URL = (
    os.getenv("OPENAI_EMBEDDING_URL")
    or os.getenv("OPENROUTER_BASE_URL")
    or (
        "https://openrouter.ai/api/v1"
        if os.getenv("OPENROUTER_API_KEY")
        else "https://api.openai.com/v1"
    )
)

def cors_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    }


def response_payload(status: int, payload: Any) -> dict[str, Any]:
    body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {
        "statusCode": status,
        "headers": cors_headers(),
        "body": body,
    }


def parse_request(request: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    method = "POST"
    headers: dict[str, Any] = {}
    body_raw: Any = None

    if request is None:
        return {}, {"method": method, "headers": headers, "query": {}}

    if isinstance(request, Mapping):
        method = str(request.get("method", method)).upper()
        headers = request.get("headers", {}) or {}
        query = request.get("queryStringParameters") or request.get("query", {}) or {}

        body_raw = request.get("body")
        if body_raw is None:
            return dict(query) if isinstance(query, Mapping) else {}, {
                "method": method,
                "headers": headers,
                "query": query,
            }

        if request.get("isBase64Encoded"):
            body_raw = base64.b64decode(body_raw)
    else:
        method = str(getattr(request, "method", method)).upper()
        headers = getattr(request, "headers", {}) or {}
        body_raw = getattr(request, "body", None)
        query = getattr(request, "query", {})

        if body_raw is None:
            return {}, {
                "method": method,
                "headers": headers,
                "query": dict(query or {}),
            }

        if isinstance(body_raw, bytes):
            body_raw = body_raw.decode("utf-8")

        if hasattr(request, "get_json"):
            try:
                return (
                    request.get_json(silent=True) or {},
                    {
                        "method": method,
                        "headers": headers,
                        "query": dict(query or {}),
                    },
                )
            except Exception:
                pass

    if body_raw is None:
        return {}, {"method": method, "headers": headers, "query": {}}

    if isinstance(body_raw, bytes):
        body_raw = body_raw.decode("utf-8")

    if isinstance(body_raw, Mapping):
        return dict(body_raw), {"method": method, "headers": headers, "query": {}}

    if isinstance(body_raw, str):
        body_raw = body_raw.strip()
        if not body_raw:
            return {}, {"method": method, "headers": headers, "query": {}}
        try:
            return json.loads(body_raw), {"method": method, "headers": headers, "query": {}}
        except Exception:
            return {}, {"method": method, "headers": headers, "query": {}}

    return {}, {"method": method, "headers": headers, "query": {}}


def validate_method(method: str) -> bool:
    return method.upper() in {"GET", "POST", "OPTIONS"}


def _stringify_headers(headers: Any) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        return {}

    normalized: dict[str, str] = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        normalized[key.lower()] = str(value) if value is not None else ""
    return normalized


def _resolve_ipv4(hostname: str) -> str | None:
    """Return an IPv4 address for hostname, or None if unavailable."""
    try:
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        if infos:
            return infos[0][4][0]
    except Exception:
        pass
    return None


def _db_connect() -> tuple[Any | None, str | None]:
    if not DB_URL:
        return None, "SUPABASE DB URL is not configured."
    if connect is None or dict_row is None:
        return None, "psycopg is not installed in this runtime."

    try:
        kwargs: dict[str, Any] = {"row_factory": dict_row}
        parsed = urllib.parse.urlparse(DB_URL)
        if parsed.hostname:
            ipv4 = _resolve_ipv4(parsed.hostname)
            if ipv4:
                kwargs["hostaddr"] = ipv4
        return connect(DB_URL, **kwargs), None
    except Exception as exc:
        return None, f"Database connection failed: {exc}"


def _ingest_preflight(
    owner: str,
    tenant_id: str,
    source: str,
    source_type: str,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "status": "ok",
        "warnings": [],
        "errors": [],
        "owner": owner,
        "tenant_id": tenant_id,
        "source": source,
        "source_type": source_type,
        "existing_rows": None,
    }

    if not owner:
        report["status"] = "failed"
        report["errors"].append("owner is empty")
    elif owner == "default_user":
        report["warnings"].append("owner is default_user. Confirm OPENBRAIN_DEFAULT_OWNER or request owner header.")

    if not tenant_id:
        report["status"] = "failed"
        report["errors"].append("tenant_id is empty")

    if not source:
        report["status"] = "failed"
        report["errors"].append("source is empty")

    if not (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")):
        report["warnings"].append(
            "No OPENROUTER_API_KEY or OPENAI_API_KEY set; ingest fallback behavior depends on configured local model."
        )

    connection, conn_error = _db_connect()
    if connection is None:
        report["status"] = "failed"
        report["errors"].append(conn_error or "Database unavailable")
        return report

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'thoughts';
                """
            )
            db_columns = {str(row["column_name"]) for row in cursor.fetchall()}
            missing = {
                "id",
                "content",
                "metadata",
                "tenant_id",
                "created_by_user_login",
                "embedding",
            } - db_columns
            if missing:
                report["status"] = "failed"
                report["errors"].append(f"thoughts table missing columns: {sorted(missing)}")
                return report

            if "source_uri" in db_columns or "metadata" in db_columns:
                match_parts: list[str] = []
                params: list[Any] = [tenant_id, owner]
                if "source_uri" in db_columns:
                    match_parts.append("COALESCE(source_uri, '') = %s")
                    params.append(source)
                if "metadata" in db_columns:
                    match_parts.append("COALESCE(metadata ->> 'source', '') = %s")
                    params.append(source)
                if match_parts:
                    query = (
                        "SELECT COUNT(*) AS total FROM public.thoughts "
                        "WHERE COALESCE(tenant_id, '') = %s "
                        "  AND COALESCE(created_by_user_login, '') = %s "
                        f"  AND ({' OR '.join(match_parts)})"
                    )
                    cursor.execute(query, tuple(params))
                    row = cursor.fetchone()
                    if isinstance(row, dict):
                        report["existing_rows"] = int(row["total"])
                    elif isinstance(row, tuple) and row:
                        report["existing_rows"] = int(row[0])
            else:
                report["warnings"].append("Unable to compute existing row count: schema missing source fields")
    except Exception as exc:
        report["warnings"].append(f"Pre-flight row-count check skipped: {exc}")
    finally:
        try:
            connection.close()
        except Exception:
            pass

    if report["existing_rows"] is not None:
        report["message"] = (
            f"{report['existing_rows']} existing rows found for this source+owner+tenant."
        )
    else:
        report["message"] = "Existing row count unavailable."

    return report


def _coalesce(*values: Any) -> str:
    for value in values:
        if isinstance(value, str):
            value = value.strip()
            if value:
                return value
    return ""


def normalize_tenant(tenant_id: str | None) -> str:
    normalized = (tenant_id or DEFAULT_TENANT).strip()
    return normalized if normalized else DEFAULT_TENANT


def normalize_owner(owner: str | None) -> str:
    normalized = (owner or DEFAULT_OWNER).strip()
    return normalized if normalized else DEFAULT_OWNER


def normalize_mode(mode: str | None) -> str:
    normalized = (mode or "explain").strip().lower()
    return normalized if normalized in ALLOWED_QUERY_MODES else "explain"


def normalize_results_count(
    value: Any,
    default: int = DEFAULT_RESULTS,
    max_value: int = MAX_RESULTS,
) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return min(max(parsed, 1), max_value)


def request_context(
    metadata: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    headers = _stringify_headers(metadata.get("headers") if isinstance(metadata, Mapping) else None)

    owner = _coalesce(
        headers.get("x-openbrain-owner"),
        headers.get("x-openbrain-user-login"),
        headers.get("x-openbrain-user-id"),
        headers.get("x-slack-user-id"),
        headers.get("x-user-id"),
        os.getenv("OPENBRAIN_DEFAULT_OWNER"),
    )
    tenant_id = _coalesce(
        headers.get("x-openbrain-tenant-id"),
        headers.get("x-tenant-id"),
        headers.get("x-family-id"),
        os.getenv("OPENBRAIN_DEFAULT_TENANT_ID"),
    )

    return normalize_owner(owner), normalize_tenant(tenant_id)


def compute_ingest_id(source_type: str, source: str, owner: str, subject: str, topic: str) -> str:
    raw = f"{owner}|{source_type}|{source}|{subject}|{topic}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def derive_subject_topic(source: str, subject: str | None, topic: str | None) -> tuple[str, str]:
    subject_value = (subject or "").strip()
    topic_value = (topic or "").strip()
    if not subject_value:
        try:
            subject_value = Path(source).stem or source
        except Exception:
            subject_value = source
    if not topic_value:
        topic_value = datetime.utcnow().strftime("%Y-%m-%d")
    return subject_value, topic_value


def build_row_payload(row: Mapping[str, Any], source_channel: str) -> dict[str, Any]:
    metadata = row.get("metadata") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}

    created_owner = (
        row.get("created_by_user_login")
        or row.get("created_by_user_id")
        or row.get("slack_username")
        or metadata.get("owner")
        or "family"
    )
    score = row.get("score")
    if isinstance(score, int | float):
        score_value = score
    else:
        score_value = None
    return {
        "score": score_value,
        "file": metadata.get("file"),
        "source": metadata.get("source") or metadata.get("uri") or source_reference(row),
        "section": metadata.get("section") or source_reference(row),
        "heading": metadata.get("heading"),
        "content_type": metadata.get("content_type") or row.get("source_type") or "slack",
        "owner": created_owner,
        "source_channel": source_channel,
        "text": row.get("content", ""),
    }


def source_reference(row: Mapping[str, Any]) -> str:
    pieces = []
    if row.get("source_type"):
        pieces.append(str(row.get("source_type")))
    if row.get("source_channel_id"):
        pieces.append(str(row.get("source_channel_id")))
    return "/".join(p for p in pieces if p)


def get_db_conn():
    if not DB_URL:
        raise RuntimeError("SUPABASE_DB_URL is not configured.")
    if connect is None:
        raise RuntimeError("psycopg is not installed in this runtime.")
    kwargs: dict[str, Any] = {"row_factory": dict_row}
    parsed = urllib.parse.urlparse(DB_URL)
    if parsed.hostname:
        ipv4 = _resolve_ipv4(parsed.hostname)
        if ipv4:
            kwargs["hostaddr"] = ipv4
    return connect(DB_URL, **kwargs)


def embedding_request(text: str) -> list[float] | None:
    embedding_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not embedding_key:
        return None

    embedding_url = (
        os.getenv("OPENAI_EMBEDDING_URL")
        or os.getenv("OPENROUTER_BASE_URL")
        or (
            "https://openrouter.ai/api/v1"
            if os.getenv("OPENROUTER_API_KEY")
            else "https://api.openai.com/v1"
        )
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {embedding_key}",
    }
    if "openrouter.ai" in embedding_url:
        headers["HTTP-Referer"] = os.getenv("OPENROUTER_HTTP_REFERER", "https://openbrain.local")
        headers["X-Title"] = os.getenv("OPENROUTER_X_TITLE", "OpenBrain Web")

    payload = json.dumps(
        {
            "model": EMBEDDING_MODEL,
            "input": text,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{embedding_url.rstrip('/')}/embeddings",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        response_payload = json.loads(response.read().decode("utf-8"))

    embedding = response_payload.get("data", [{}])[0].get("embedding")
    if isinstance(embedding, list):
        return [float(x) for x in embedding]
    return None


def _vector_param(embedding: list[float]) -> str:
    return "[" + ",".join(f"{float(item):.8f}" for item in embedding) + "]"


def search_vector_candidates(
    query_embedding: list[float],
    owner: str,
    tenant_id: str,
    max_results: int,
) -> list[dict[str, Any]]:
    vector_param = _vector_param(query_embedding)
    owner_condition = ""
    params: list[Any] = [vector_param, tenant_id]
    if owner:
        owner_condition = (
            " AND (COALESCE(created_by_user_login, created_by_user_id, "
            "slack_username, '') = %s OR metadata ->> 'owner' = %s)"
        )
        params.extend([owner, owner])
    params.extend([vector_param, max_results])

    query = f"""
    SELECT
      id,
      content,
      tenant_id,
      source_type,
      source_team_id,
      source_workspace_id,
      source_channel_id,
      created_by_user_id,
      created_by_user_login,
      slack_username,
      metadata,
      (embedding <=> %s::vector) AS score
    FROM public.thoughts
    WHERE COALESCE(tenant_id, 'family') = %s
      AND embedding IS NOT NULL
      {owner_condition}
    ORDER BY embedding <=> %s::vector
    LIMIT %s;
    """

    with get_db_conn() as conn:
        rows = conn.execute(query, params).fetchall()
    return [build_row_payload(dict(row), "vector") for row in rows]


def search_keyword_candidates(
    query: str,
    owner: str,
    tenant_id: str,
    max_results: int,
) -> list[dict[str, Any]]:
    owner_condition = ""
    ts_query = _ts_query(query)
    params: list[Any] = [ts_query, tenant_id, ts_query]
    if owner:
        owner_condition = (
            " AND (COALESCE(created_by_user_login, created_by_user_id, "
            "slack_username, '') = %s OR metadata ->> 'owner' = %s)"
        )
        params.extend([owner, owner])
    params.append(max_results)

    query_sql = f"""
    SELECT
      id,
      content,
      tenant_id,
      source_type,
      source_team_id,
      source_workspace_id,
      source_channel_id,
      created_by_user_id,
      created_by_user_login,
      slack_username,
      metadata,
      ts_rank(
        to_tsvector('english', coalesce(content, '')),
        websearch_to_tsquery('english', %s)
      ) AS score
    FROM public.thoughts
    WHERE COALESCE(tenant_id, 'family') = %s
      AND to_tsvector('english', coalesce(content, '')) @@ websearch_to_tsquery('english', %s)
      {owner_condition}
    ORDER BY score DESC NULLS LAST
    LIMIT %s;
    """

    with get_db_conn() as conn:
        rows = conn.execute(query_sql, params).fetchall()
    return [build_row_payload(dict(row), "keyword") for row in rows]


def _ts_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", (query or "").strip())
    return cleaned


def retrieve_thoughts(
    query: str,
    n_results: int,
    owner: str,
    tenant_id: str,
) -> list[dict[str, Any]]:
    max_results = min(max(1, n_results) * 2, MAX_RESULTS)

    vector_rows: list[dict[str, Any]] = []
    try:
        embedding = embedding_request(query)
        if embedding is not None:
            vector_rows = search_vector_candidates(embedding, owner, tenant_id, max_results)
    except Exception:
        vector_rows = []

    keyword_rows = []
    try:
        keyword_rows = search_keyword_candidates(query, owner, tenant_id, max_results)
    except Exception:
        keyword_rows = []

    if not keyword_rows and not vector_rows:
        return []

    final: list[dict[str, Any]] = []
    keyword_seen: set[tuple[Any, Any]] = set()
    for row in keyword_rows:
        key = (row.get("file"), row.get("source"))
        if key in keyword_seen:
            continue
        keyword_seen.add(key)
        final.append(row)
        if len(final) >= max_results:
            break

    for row in vector_rows:
        key = (row.get("file"), row.get("source"))
        if key in keyword_seen:
            continue
        keyword_seen.add(key)
        final.append(row)
        if len(final) >= max_results:
            break

    return final[:max_results]


def run_tutor_payload(
    query: str,
    mode: str,
    results: list[dict[str, Any]],
    student_attempt: str | None = None,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    context_chunks = [
        {
            "source": result.get("source"),
            "file": result.get("file"),
            "section": result.get("section"),
            "heading": result.get("heading"),
            "text": result.get("text", ""),
        }
        for result in results
    ]

    if build_tutor_packet is None:
        tutor_payload = {
            "mode": normalize_mode(mode),
            "rules": [
                "Ask the student to try first.",
                "Use short, simple language for a middle school learner.",
                "Explain ideas step by step.",
                "Encourage effort and curiosity before confirming answers.",
            ],
            "tutor_prompt": "Tutor module unavailable in this runtime.",
            "context_used": context_chunks,
        }
    else:
        tutor_payload = build_tutor_packet(
            normalize_mode(mode),
            query,
            context_chunks,
            student_attempt,
        )

    return (
        tutor_payload.get("mode", normalize_mode(mode)),
        context_chunks,
        tutor_payload,
    )


def query_payload(
    payload: Mapping[str, Any],
    method_metadata: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return 400, {
            "error": "validation_error",
            "message": "Malformed JSON payload.",
            "status": 400,
        }

    query = (payload.get("query") or "").strip()
    if not query:
        return 400, {
            "error": "validation_error",
            "message": "Query is required.",
            "status": 400,
        }

    mode = normalize_mode(payload.get("mode"))
    n_results = normalize_results_count(payload.get("n_results"), DEFAULT_RESULTS)
    owner, tenant_id = request_context(method_metadata)
    student_attempt = payload.get("student_attempt")
    results = retrieve_thoughts(query, n_results, owner, tenant_id)

    (
        normalized_mode,
        context_chunks,
        tutor_packet,
    ) = run_tutor_payload(query, mode, results, student_attempt)

    return 200, {
        "mode": normalized_mode,
        "question": query,
        "rules": tutor_packet.get("rules", []),
        "tutor_prompt": tutor_packet.get("tutor_prompt", ""),
        "context_used": context_chunks,
        "results": results,
    }


def search_payload(
    payload: Mapping[str, Any],
    method_metadata: Mapping[str, Any],
) -> tuple[int, Any]:
    if not isinstance(payload, Mapping):
        return 400, {
            "error": "validation_error",
            "message": "Malformed JSON payload.",
            "status": 400,
        }

    query = (payload.get("query") or "").strip()
    if not query:
        query = (method_metadata.get("query", {}).get("query") or "").strip()

    if not query:
        return 400, {
            "error": "validation_error",
            "message": "Query is required.",
            "status": 400,
        }

    n_results = normalize_results_count(payload.get("n_results"), DEFAULT_RESULTS)
    owner, tenant_id = request_context(method_metadata)
    results = retrieve_thoughts(query, n_results, owner, tenant_id)
    return 200, {"results": results, "count": len(results)}


def _normalize_bulk_items(
    payload: Mapping[str, Any],
    allowed: set[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    candidate_items = payload.get("sources")
    if candidate_items is None:
        candidate_items = payload.get("items")
    if not isinstance(candidate_items, list):
        return [], ["payload missing valid 'sources' list"]

    default_source_type = (payload.get("source_type") or "").strip().lower()
    default_subject = payload.get("subject")
    default_topic = payload.get("topic")
    if candidate_items:
        max_items = len(candidate_items)
    else:
        max_items = 0
    if max_items == 0:
        return [], ["sources list is empty"]

    normalized_items: list[dict[str, Any]] = []
    errors: list[str] = []

    for index, item in enumerate(candidate_items):
        entry_subject, entry_topic = derive_subject_topic(
            str(item.get("source")) if isinstance(item, Mapping) else str(item),
            default_subject,
            default_topic,
        )

        if isinstance(item, Mapping):
            source = (item.get("source") or "").strip()
            source_type = (item.get("source_type") or default_source_type).strip().lower()
            item_subject = item.get("subject")
            item_topic = item.get("topic")
        else:
            source = str(item).strip()
            source_type = default_source_type
            item_subject = None
            item_topic = None

        if item_subject is not None and str(item_subject).strip():
            entry_subject = str(item_subject).strip()
        if item_topic is not None and str(item_topic).strip():
            entry_topic = str(item_topic).strip()

        if not source_type:
            errors.append(f"item {index}: missing source_type")
            continue
        if source_type not in allowed:
            errors.append(f"item {index}: source_type '{source_type}' is not supported")
            continue

        if not source:
            errors.append(f"item {index}: source field is missing or empty")
            continue

        if source_type in {"pdf", "docx", "obsidian", "url"}:
            reachable, reason = _source_reachable(source_type, source)
            if not reachable:
                errors.append(f"item {index}: source not reachable: {reason}")
                continue

        item_owner_status = "queued" if source_type != "obsidian" else "accepted"
        normalized_items.append(
            {
                "source_type": source_type,
                "source": source,
                "subject": entry_subject,
                "topic": entry_topic,
                "status": item_owner_status,
                "details": [],
            }
        )

    return normalized_items, errors


def ingest_payload(
    payload: Mapping[str, Any],
    method_metadata: Mapping[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return 400, {
            "error": "validation_error",
            "message": "Malformed JSON payload.",
            "status": 400,
        }

    source_type = (payload.get("source_type") or "").strip().lower()
    source = (payload.get("source") or "").strip()
    owner, _tenant_id = request_context(method_metadata)
    subject, topic = derive_subject_topic(source, payload.get("subject"), payload.get("topic"))

    allowed = {"obsidian", "pdf", "docx", "url"}
    status = "failed"
    message = ""
    details: list[str] = []
    preflight_summary: dict[str, Any] | None = None
    bulk_preflight: list[dict[str, Any]] = []

    if "sources" in payload or "items" in payload:
        normalized_items, item_errors = _normalize_bulk_items(payload, allowed)
        if not normalized_items:
            raw_sources = payload.get("sources")
            raw_signature = []
            if isinstance(raw_sources, list):
                raw_signature = [str(item) for item in raw_sources]

            return 200, {
                "ingest_id": compute_ingest_id(
                    "bulk",
                    "|".join(raw_signature),
                    owner,
                    subject,
                    topic,
                ),
                "status": "failed",
                "source_type": "bulk",
                "source": "",
                "owner": owner,
                "subject": subject,
                "topic": topic,
                "message": "Ingest failed: no valid bulk items.",
                "details": item_errors,
                "items": [],
                "summary": {
                    "total": 0,
                    "accepted": 0,
                    "failed": 0,
                    "queued": 0,
                },
                "preflight": {"status": "failed", "items": []},
            }

        accepted = 0
        queued = 0
        failed = len(item_errors)
        for item in normalized_items:
            item_preflight = _ingest_preflight(
                owner,
                _tenant_id,
                item["source"],
                item["source_type"],
            )
            item["preflight"] = item_preflight
            bulk_preflight.append(item_preflight)
            if item_preflight.get("status") == "failed":
                item["status"] = "failed"
                item["details"].append("preflight checks failed")
                failed += 1

            item_status = item["status"]
            item["ingest_id"] = compute_ingest_id(
                item["source_type"],
                item["source"],
                owner,
                item["subject"],
                item["topic"],
            )
            if item_status == "accepted":
                accepted += 1
            else:
                queued += 1

        status = "failed" if any(item.get("status") == "failed" for item in normalized_items) else "accepted"
        if status == "failed":
            message = "Ingest request blocked by pre-flight checks."
            details.extend(item_errors)
            details.extend(
                sorted({error for preflight in bulk_preflight for error in preflight.get("errors", [])})
            )
        else:
            message = (
                f"Bulk ingest accepted for {len(normalized_items)} source item(s)."
                if len(normalized_items) > 1
                else "Ingest request accepted."
            )
        return 200, {
            "ingest_id": compute_ingest_id(
                "bulk",
                "|".join(item["source"] for item in normalized_items),
                owner,
                subject,
                topic,
            ),
            "status": status,
            "source_type": "bulk",
            "source": normalized_items[0]["source"],
            "owner": owner,
            "subject": subject,
            "topic": topic,
            "message": message,
            "details": item_errors,
            "items": normalized_items,
            "summary": {
                "total": len(normalized_items) + len(item_errors),
                "accepted": accepted,
                "queued": queued,
                "failed": failed,
                "errors": len(item_errors),
            },
            "preflight": {
                "status": status,
                "items": bulk_preflight,
            },
        }

    if not source:
        message = "Ingest failed: source is required."
        details.append("source field is missing or empty")
    elif source_type not in allowed:
        message = f"Ingest failed: unsupported source_type '{source_type}'."
        details.append(f"source_type '{source_type}' is not supported")
    else:
        reachable, reason = _source_reachable(source_type, source)
        if not reachable:
            message = "Ingest failed: source is not reachable."
            details.append(reason)
        elif source_type == "obsidian":
            preflight_summary = _ingest_preflight(owner, _tenant_id, source, source_type)
            if preflight_summary.get("status") == "failed":
                status = "failed"
                message = "Ingest blocked by pre-flight checks."
                details.extend(preflight_summary.get("errors", []))
            else:
                status = "accepted"
                message = "Ingest request accepted."
                if preflight_summary.get("warnings"):
                    details.extend(preflight_summary.get("warnings", []))
        else:
            preflight_summary = _ingest_preflight(owner, _tenant_id, source, source_type)
            if preflight_summary.get("status") == "failed":
                status = "failed"
                message = "Ingest blocked by pre-flight checks."
                details.extend(preflight_summary.get("errors", []))
            else:
                status = "queued"
                message = "Ingest request accepted. Processing is currently queued in the MCP scaffold."
                if preflight_summary.get("warnings"):
                    details.extend(preflight_summary.get("warnings", []))

    return 200, {
        "ingest_id": compute_ingest_id(source_type, source, owner, subject, topic),
        "status": status,
        "source_type": source_type,
        "source": source,
        "owner": owner,
        "subject": subject,
        "topic": topic,
        "message": message,
        "details": details,
        "preflight": preflight_summary,
    }


def _source_reachable(source_type: str, source: str) -> tuple[bool, str | None]:
    if source_type == "obsidian":
        if not source.strip():
            return False, "source is required"
        return True, None

    if source_type == "url":
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return False, "invalid URL format"

        request = urllib.request.Request(source, method="HEAD")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                if response.status >= 400:
                    return False, f"url returned HTTP {response.status}"
        except urllib.error.HTTPError as exc:
            if exc.code != 405:
                return False, f"url returned HTTP {exc.code}"
        except Exception as exc:
            return False, f"url is not reachable: {exc}"
        return True, None

    path = Path(source)
    if not path.exists():
        return False, f"{source_type} source not found at path"
    if source_type in {"pdf", "docx"} and not path.is_file():
        return False, f"{source_type} source must be a file"
    return True, None
