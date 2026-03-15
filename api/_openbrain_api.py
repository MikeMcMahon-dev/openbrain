from __future__ import annotations

import base64
import hashlib
import json
import os
import re
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
    os.getenv("SUPABASE_DB_URL") or os.getenv("SUPABASE_DATABASE_URL") or os.getenv("DATABASE_URL")
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
    return connect(DB_URL, row_factory=dict_row)


def embedding_request(text: str) -> list[float] | None:
    if not EMBEDDING_KEY:
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {EMBEDDING_KEY}",
    }
    if "openrouter.ai" in EMBEDDING_URL:
        headers["HTTP-Referer"] = os.getenv("OPENROUTER_HTTP_REFERER", "https://openbrain.local")
        headers["X-Title"] = os.getenv("OPENROUTER_X_TITLE", "OpenBrain Web")

    payload = json.dumps(
        {
            "model": EMBEDDING_MODEL,
            "input": text,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{EMBEDDING_URL.rstrip('/')}/embeddings",
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
            status = "accepted"
            message = "Ingest request accepted."
        else:
            status = "queued"
            message = "Ingest request accepted. Processing is currently queued in the MCP scaffold."

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
    }


def _source_reachable(source_type: str, source: str) -> tuple[bool, str | None]:
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
