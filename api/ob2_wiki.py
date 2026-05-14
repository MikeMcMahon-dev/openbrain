from __future__ import annotations

import json
import os
import urllib.request
from typing import Any

from api._openbrain_api import (
    get_db_conn,
    parse_request,
    require_auth_owner,
    response_payload,
)

VALID_PAGE_TYPES = {"system-state", "pending-tasks", "topology", "summary", "event-log"}
_COMPLETION_MODEL = os.getenv("OPENBRAIN_COMPLETION_MODEL", "gpt-4o-mini")


def _call_completion(prompt: str) -> str | None:
    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        return None

    base_url = (
        os.getenv("OPENAI_BASE_URL")
        or (
            "https://openrouter.ai/api/v1"
            if os.getenv("OPENROUTER_API_KEY")
            else "https://api.openai.com/v1"
        )
    )

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if "openrouter.ai" in base_url:
        headers["HTTP-Referer"] = os.getenv("OPENROUTER_HTTP_REFERER", "https://openbrain.local")
        headers["X-Title"] = os.getenv("OPENROUTER_X_TITLE", "OpenBrain Web")

    payload = json.dumps({
        "model": _COMPLETION_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a technical wiki compiler. Given a set of knowledge records, "
                    "synthesize them into a clear, accurate, well-structured markdown wiki page. "
                    "Present facts precisely. Do not add information not in the records. "
                    "Format with headers and bullet points where appropriate."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=payload,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    choices = data.get("choices", [])
    if choices:
        return (choices[0].get("message") or {}).get("content", "").strip() or None
    return None


def handle_compile_wiki(request: Any) -> dict[str, Any]:
    body, metadata = parse_request(request)

    auth_err, _resolved_owner = require_auth_owner(metadata)
    if auth_err:
        return auth_err

    page_name = (body.get("page_name") or "").strip()
    page_type = (body.get("page_type") or "summary").strip()
    domain = (body.get("domain") or "").strip()

    if not page_name:
        return response_payload(400, {"error": "bad_request", "message": "page_name is required"})
    if not domain:
        return response_payload(400, {"error": "bad_request", "message": "domain is required"})
    if page_type not in VALID_PAGE_TYPES:
        return response_payload(400, {
            "error": "bad_request",
            "message": f"page_type must be one of {sorted(VALID_PAGE_TYPES)}",
        })

    system = body.get("system") or None
    try:
        limit = int(body.get("limit") or 20)
        limit = max(1, min(limit, 50))
    except (ValueError, TypeError):
        limit = 20

    where_clauses = ["status = 'current'", "domain = %s"]
    params: list[Any] = [domain]
    if system:
        where_clauses.append("system = %s")
        params.append(system)
    params.append(limit)

    try:
        with get_db_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT id, content, tags, environment, system, valid_from
                FROM public.knowledge
                WHERE {" AND ".join(where_clauses)}
                ORDER BY valid_from DESC
                LIMIT %s
                """,
                params,
            ).fetchall()
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc)})

    if not rows:
        suffix = f", system={system}" if system else ""
        return response_payload(404, {
            "error": "not_found",
            "message": f"No current knowledge records found for domain={domain}{suffix}",
        })

    source_ids = [str(row["id"]) for row in rows]

    records_text = "\n\n".join(
        f"[Record {i + 1}] Tags: {row['tags']} | Environment: {row['environment']} | "
        f"Valid from: {row['valid_from']}\n{row['content']}"
        for i, row in enumerate(rows)
    )
    prompt = (
        f"Compile a wiki page '{page_name}' of type '{page_type}' for domain '{domain}'"
        + (f", system '{system}'" if system else "")
        + f".\n\nSource knowledge records ({len(rows)} records):\n\n{records_text}"
    )

    compiled_content = None
    try:
        compiled_content = _call_completion(prompt)
    except Exception:
        pass

    if not compiled_content:
        compiled_content = (
            f"# {page_name}\n\n"
            f"*Compiled from {len(rows)} knowledge records — LLM synthesis unavailable*\n\n"
            + "\n\n---\n\n".join(
                f"**Tags:** {row['tags']}\n\n{row['content']}" for row in rows
            )
        )

    try:
        with get_db_conn() as conn:
            result = conn.execute(
                """
                INSERT INTO public.wiki_pages
                    (page_name, page_type, content, compiled_from, domain, system, is_stale)
                VALUES (%s, %s, %s, %s::uuid[], %s, %s, false)
                ON CONFLICT (page_name) DO UPDATE SET
                    content = EXCLUDED.content,
                    compiled_from = EXCLUDED.compiled_from,
                    domain = EXCLUDED.domain,
                    system = EXCLUDED.system,
                    page_type = EXCLUDED.page_type,
                    generated_at = now(),
                    is_stale = false
                RETURNING id, generated_at
                """,
                [page_name, page_type, compiled_content, source_ids, domain, system],
            ).fetchone()
            wiki_id = str(result["id"]) if result else None
            generated_at_val = result["generated_at"] if result else None
            generated_at = (
                generated_at_val.isoformat()
                if generated_at_val and hasattr(generated_at_val, "isoformat")
                else str(generated_at_val) if generated_at_val else None
            )
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc)})

    return response_payload(200, {
        "status": "compiled",
        "page_name": page_name,
        "page_type": page_type,
        "id": wiki_id,
        "generated_at": generated_at,
        "source_count": len(rows),
        "compiled_from": source_ids,
    })


def handle_get_wiki(request: Any, page_name: str) -> dict[str, Any]:
    _body, metadata = parse_request(request)

    auth_err, _owner = require_auth_owner(metadata)
    if auth_err:
        return auth_err

    try:
        with get_db_conn() as conn:
            row = conn.execute(
                """
                SELECT id, page_name, page_type, content, compiled_from,
                       domain, system, generated_at, is_stale
                FROM public.wiki_pages
                WHERE page_name = %s
                """,
                [page_name],
            ).fetchone()
    except Exception as exc:
        return response_payload(500, {"error": "db_error", "message": str(exc)})

    if not row:
        return response_payload(404, {
            "error": "not_found",
            "message": f"Wiki page '{page_name}' not found",
        })

    r = dict(row)
    r["id"] = str(r["id"])
    if r.get("generated_at") and hasattr(r["generated_at"], "isoformat"):
        r["generated_at"] = r["generated_at"].isoformat()
    if r.get("compiled_from"):
        r["compiled_from"] = [str(x) for x in r["compiled_from"]]

    return response_payload(200, r)
