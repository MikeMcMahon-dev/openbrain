#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import json
import ssl
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _read_runtime_env() -> None:
    """Load local environment defaults without requiring dotenv."""
    for path in (PROJECT_ROOT / ".env", PROJECT_ROOT / ".env.local"):
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and value and not os.getenv(key):
                    os.environ[key] = value
        except Exception:
            continue


_read_runtime_env()


def _make_result(name: str, status: int, payload: Any, expected: int) -> tuple[bool, str]:
    body_str = ""
    if isinstance(payload, (dict, list)):
        body_str = json.dumps(payload)
    else:
        body_str = str(payload)
    if status != expected:
        return (
            False,
            f"{name}: expected {expected}, got {status}; body={body_str[:320]}",
        )
    return True, f"{name}: ok ({status})"


def _validate_payload_shape(
    payload: Any,
    path: str,
    expected_owner: str | None = None,
) -> tuple[bool, str]:
    if path not in {"/api/ingest", "/ingest"}:
        return True, "ok"

    if not isinstance(payload, dict):
        return False, f"{path}: expected JSON object payload"

    required_keys = {
        "ingest_id",
        "status",
        "source_type",
        "source",
        "owner",
        "subject",
        "topic",
        "message",
        "details",
    }
    missing = sorted(required_keys - set(payload.keys()))
    if missing:
        return False, f"{path}: missing keys {missing}"

    if payload.get("status") not in {"accepted", "queued"}:
        return False, f"{path}: unexpected status '{payload.get('status')}'"

    if expected_owner is not None and payload.get("owner") != expected_owner:
        return False, f"{path}: owner={payload.get('owner')} expected_owner={expected_owner}"

    return True, f"{path}: ok ({payload.get('status')})"


def run_case(
    request: dict[str, Any],
    expected: int,
    validate_payload: bool = False,
    expected_owner: str | None = None,
) -> tuple[bool, str]:
    try:
        from api.app import handler
    except Exception as exc:
        return False, f"import_error: api.app could not be imported ({type(exc).__name__}: {exc})"

    response = handler(request)
    if not isinstance(response, dict):
        return False, f"{response=}"

    status = response.get("statusCode")
    body = response.get("body")
    if isinstance(body, str):
        try:
            body_payload: Any = json.loads(body)
        except Exception:
            body_payload = body
    else:
        body_payload = body
    route = request.get("path") if isinstance(request, dict) else "unknown"
    ok, message = _make_result(route, status, body_payload, expected)
    if not ok:
        return False, message

    if validate_payload:
        ok_payload, detail = _validate_payload_shape(
            body_payload,
            route,
            expected_owner=expected_owner,
        )
        if not ok_payload:
            return False, detail
        return True, detail

    return ok, message


def _supabase_url() -> str | None:
    return (
        os.getenv("OPENBRAIN_SUPABASE_CONNECTION_STRING")
        or os.getenv("SUPABASE_DB_URL")
        or os.getenv("SUPABASE_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )


def _count_ingest_rows_for_source(source: str, tenant_id: str, owner: str) -> int | None:
    db_url = _supabase_url()
    if not db_url:
        return None
    try:
        from psycopg import connect
        from psycopg.rows import dict_row

        conn = connect(db_url, row_factory=dict_row)
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM public.thoughts
            WHERE tenant_id = %s
              AND COALESCE(created_by_user_login, '') = %s
              AND COALESCE(metadata ->> 'source', '') = %s
            """,
            (tenant_id, owner, source),
        )
        total = cur.fetchone()["total"]
        cur.close()
        conn.close()
        return int(total)
    except Exception as exc:
        print(f"Skipping idempotency DB check: {type(exc).__name__}: {exc}")
        return None


def _run_local_ingest_call(source: str, owner: str | None, path: str = "/api/ingest") -> tuple[bool, str]:
    body = {"source_type": "obsidian", "source": source}
    if owner:
        body["owner"] = owner
    return run_case(
        {
            "path": path,
            "method": "POST",
            "body": json.dumps(body),
            "headers": {"Content-Type": "application/json"},
        },
        200,
        True,
        expected_owner=owner,
    )


def _smoke_local_idempotency_check(source: str, owner: str | None) -> int:
    normalized_owner = (owner or os.getenv("OPENBRAIN_DEFAULT_OWNER") or "mike.mcmahon67").strip()
    tenant_id = (os.getenv("OPENBRAIN_DEFAULT_TENANT_ID") or "family").strip() or "family"

    before = _count_ingest_rows_for_source(source, tenant_id, normalized_owner)
    if before is None:
        print("Idempotency check skipped: DB URL not available or DB query failed.")
        return 0

    ok1, message1 = _run_local_ingest_call(source, normalized_owner)
    print(message1)
    if not ok1:
        print("Idempotency check failed on first ingest call.")
        return 1
    after_first = _count_ingest_rows_for_source(source, tenant_id, normalized_owner)
    if after_first is None:
        return 0

    ok2, message2 = _run_local_ingest_call(source, normalized_owner)
    print(message2)
    if not ok2:
        print("Idempotency check failed on second ingest call.")
        return 1
    after_second = _count_ingest_rows_for_source(source, tenant_id, normalized_owner)
    if after_second is None:
        return 0

    print(f"Idempotency check for source='{source}', owner='{normalized_owner}': before={before}, after_first={after_first}, after_second={after_second}")
    if after_first < before:
        print("Idempotency check failed: first call reduced row count.")
        return 1
    if after_second != after_first:
        print("Idempotency check failed: second call changed row count.")
        return 1
    print("Idempotency check passed.")
    return 0


def smoke_local(idempotency_source: str | None = None, idempotency_owner: str | None = None) -> int:
    query_body = json.dumps({"query": "test"})
    _token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    _auth = {"Authorization": f"Bearer {_token}"} if _token else {}
    cases = [
        ({"path": "/", "method": "GET"}, 200),
        ({"path": "/health", "method": "GET"}, 200),
        ({"path": "/api/health", "method": "GET"}, 200),
        (
            {
                "path": "/query",
                "method": "POST",
                "body": query_body,
                "headers": _auth,
            },
            200,
        ),
        (
            {
                "path": "/api/query",
                "method": "POST",
                "body": query_body,
                "headers": _auth,
            },
            200,
        ),
        (
            {
                "path": "/search",
                "method": "POST",
                "body": query_body,
                "headers": _auth,
            },
            200,
        ),
        (
            {
                "path": "/api/search",
                "method": "POST",
                "body": query_body,
                "headers": _auth,
            },
            200,
        ),
        (
            {
                "path": "/generate_quiz",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": {},
            },
            200,
        ),
        (
            {
                "path": "/api/generate_quiz",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": {},
            },
            200,
        ),
        (
            {
                "path": "/generate_flashcards",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": {},
            },
            200,
        ),
        (
            {
                "path": "/api/generate_flashcards",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": {},
            },
            200,
        ),
        (
            {
                "path": "/ingest",
                "method": "POST",
                "body": json.dumps({"source_type": "obsidian", "source": "/tmp"}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/api/ingest",
                "method": "POST",
                "body": json.dumps({"source_type": "obsidian", "source": "/tmp"}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
            True,
        ),
        (
            {
                "path": "/api/ingest",
                "method": "POST",
                "body": json.dumps(
                    {"source_type": "obsidian", "source": "/tmp", "owner": "evil-user"}
                ),
                "headers": {
                    "Content-Type": "application/json",
                    "x-openbrain-owner": "tenant-a-owner",
                    **_auth,
                },
            },
            200,
            True,
            "tenant-a-owner",
        ),
        (
            {
                "path": "/openbrain_query",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": _auth,
            },
            200,
        ),
        (
            {
                "path": "/openbrain_generate_quiz",
                "method": "POST",
                "body": json.dumps(
                    {"tool_input": {"query": "test"}, "tool_name": "openbrain_generate_quiz"}
                ),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/openbrain_generate_flashcards",
                "method": "POST",
                "body": json.dumps(
                    {
                        "arguments": {"query": "test"},
                        "name": "openbrain_generate_flashcards",
                    }
                ),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/openbrain_ingest",
                "method": "POST",
                "body": json.dumps(
                    {
                        "tool_input": {
                            "source_type": "obsidian",
                            "source": "/tmp",
                        }
                    }
                ),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
            True,
        ),
        # Claude adapter — native tool_use envelope
        (
            {
                "path": "/claude_query",
                "method": "POST",
                "body": json.dumps({"type": "tool_use", "name": "claude_query", "input": {"query": "test"}}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/claude_generate_quiz",
                "method": "POST",
                "body": json.dumps({"type": "tool_use", "name": "claude_generate_quiz", "input": {"query": "test"}}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/claude_generate_flashcards",
                "method": "POST",
                "body": json.dumps({"type": "tool_use", "name": "claude_generate_flashcards", "input": {"query": "test"}}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/claude_ingest",
                "method": "POST",
                "body": json.dumps({"type": "tool_use", "name": "claude_ingest", "input": {"source_type": "text", "source": "smoke test"}}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
            True,
        ),
        # tools/ prefix variants
        (
            {
                "path": "/tools/claude_query",
                "method": "POST",
                "body": json.dumps({"input": {"query": "test"}}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        ({"path": "/bogus-path", "method": "GET"}, 404),
        # session_report — 400 when missing owner (auth passes first)
        (
            {
                "path": "/session_report",
                "method": "POST",
                "body": json.dumps({"recipients": ["test@example.com"]}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            400,
        ),
        # session_report — 400 when missing recipients
        (
            {
                "path": "/session_report",
                "method": "POST",
                "body": json.dumps({"owner": "annie"}),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            400,
        ),
        # session_report — cross-tenant: mismatched owner → 403 when token map is active;
        # 200 (skipped) when no token map configured (dev with no auth)
        (
            {
                "path": "/session_report",
                "method": "POST",
                "body": json.dumps(
                    {"owner": "nobody-has-this-owner-xyz", "recipients": ["test@example.com"]}
                ),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            403 if os.getenv("OPENBRAIN_TOKEN_OWNER_MAP") else 200,
        ),
        # cron endpoint — 200 with no REPORT_CONFIGS set (returns skipped)
        (
            {
                "path": "/api/cron/session_report",
                "method": "GET",
                "headers": {},
            },
            200,
        ),
    ]

    failed = 0
    for case in cases:
        if len(case) == 4:
            request, expected, validate_payload, expected_owner = case
        elif len(case) == 3:
            request, expected, validate_payload = case
            expected_owner = None
        else:
            request, expected = case
            validate_payload = False
            expected_owner = None

        ok, message = run_case(request, expected, validate_payload, expected_owner)
        print(message)
        if not ok:
            failed += 1

    if idempotency_source:
        print(f"\nRunning local ingest idempotency check for source: {idempotency_source}")
        failed += _smoke_local_idempotency_check(idempotency_source, idempotency_owner)

    return failed


def _call_live(
    url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    target = urllib.parse.urljoin(url.rstrip("/") + "/", path.lstrip("/"))
    payload = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req_headers = {"x-openbrain-owner": "tenant-a-owner"}
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(
        target,
        data=payload,
        headers=req_headers,
        method=method,
    )
    context = ssl.create_default_context()
    with urllib.request.urlopen(req, context=context, timeout=20) as resp:
        response_body = resp.read().decode("utf-8")
        status = getattr(resp, "status", 200)
    try:
        return status, json.loads(response_body)
    except Exception:
        return status, response_body


def smoke_live(base_url: str) -> int:
    _token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    _auth = {"Authorization": f"Bearer {_token}"} if _token else {}
    cases = [
        ("/", None, 200),
        ("/health", None, 200),
        ("/api/health", None, 200),
        ("/query", {"query": "test"}, 200, _auth),
        ("/api/query", {"query": "test"}, 200, _auth),
        ("/search", {"query": "test"}, 200, _auth),
        ("/api/search", {"query": "test"}, 200, _auth),
        ("/generate_quiz", {"query": "test"}, 200),
        ("/api/generate_quiz", {"query": "test"}, 200),
        ("/generate_flashcards", {"query": "test"}, 200),
        ("/api/generate_flashcards", {"query": "test"}, 200),
        ("/ingest", {"source_type": "obsidian", "source": "/tmp"}, 200, _auth),
        (
            "/api/ingest",
            {"source_type": "obsidian", "source": "/tmp", "owner": "evil-user"},
            200,
            {"x-openbrain-owner": "tenant-a-owner", **_auth},
            "tenant-a-owner",
        ),
        ("/openbrain_query", {"query": "test"}, 200, _auth),
        (
            "/openbrain_generate_quiz",
            {"tool_input": {"query": "test"}, "tool_name": "openbrain_generate_quiz"},
            200,
            _auth,
        ),
        (
            "/openbrain_generate_flashcards",
            {"arguments": {"query": "test"}, "name": "openbrain_generate_flashcards"},
            200,
            _auth,
        ),
        (
            "/openbrain_ingest",
            {"tool_input": {"source_type": "obsidian", "source": "/tmp"}},
            200,
            _auth,
        ),
        # Claude adapter — live, with auth token
        (
            "/claude_query",
            {"type": "tool_use", "name": "claude_query", "input": {"query": "terraform"}},
            200,
            {"Authorization": f"Bearer {os.getenv('OPENBRAIN_TOOL_ACCESS_TOKEN', '')}"},
        ),
        (
            "/claude_generate_quiz",
            {"type": "tool_use", "name": "claude_generate_quiz", "input": {"query": "terraform"}},
            200,
            {"Authorization": f"Bearer {os.getenv('OPENBRAIN_TOOL_ACCESS_TOKEN', '')}"},
        ),
        (
            "/claude_generate_flashcards",
            {"type": "tool_use", "name": "claude_generate_flashcards", "input": {"query": "terraform"}},
            200,
            {"Authorization": f"Bearer {os.getenv('OPENBRAIN_TOOL_ACCESS_TOKEN', '')}"},
        ),
        (
            "/claude_ingest",
            {"type": "tool_use", "name": "claude_ingest", "input": {"source_type": "text", "source": "live smoke test note"}},
            200,
            {"Authorization": f"Bearer {os.getenv('OPENBRAIN_TOOL_ACCESS_TOKEN', '')}"},
        ),
        # Auth rejection — wrong token must return 401
        (
            "/claude_query",
            {"input": {"query": "terraform"}},
            401,
            {"Authorization": "Bearer invalid-token"},
        ),
        # session_report — missing owner → 400
        (
            "/session_report",
            {"recipients": ["test@example.com"]},
            400,
            _auth,
        ),
        # session_report — missing recipients → 400
        (
            "/session_report",
            {"owner": "annie"},
            400,
            _auth,
        ),
        # session_report — cross-tenant guard: mismatched owner → 403
        # (confirms TOKEN_OWNER_MAP enforcement is active in production)
        (
            "/session_report",
            {"owner": "nobody-has-this-owner-xyz", "recipients": ["test@example.com"]},
            403,
            _auth,
        ),
        # cron endpoint — no REPORT_CONFIGS → 200 skipped
        (
            "/api/cron/session_report",
            None,
            200,
        ),
    ]

    failed = 0
    for case in cases:
        expected_owner = None
        req_headers = None
        if len(case) == 5:
            path, payload, expected, req_headers, expected_owner = case
        elif len(case) == 4:
            path, payload, expected, req_headers = case
        else:
            path, payload, expected = case
        try:
            status, body = _call_live(
                base_url,
                "POST" if payload is not None else "GET",
                path,
                payload,
                req_headers,
            )
            ok, message = _make_result(path, status, body, expected)
            if ok:
                ok, detail = _validate_payload_shape(
                    body,
                    path,
                    expected_owner=expected_owner,
                )
                if not ok:
                    message = detail
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            ok, message = _make_result(path, exc.code, body, expected)
        except Exception as exc:
            traceback.print_exc()
            ok = False
            message = f"{path}: request failed: {exc}"

        print(message)
        if not ok:
            failed += 1

    return failed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live",
        help="Run smoke checks against a live deployment URL, e.g. https://example.vercel.app",
    )
    parser.add_argument(
        "--idempotency-source",
        help="Optional source path to run a local /api/ingest idempotency check.",
    )
    parser.add_argument(
        "--idempotency-owner",
        help="Expected owner for local /api/ingest idempotency check. Defaults to OPENBRAIN_DEFAULT_OWNER.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.live:
        print(f"Running live smoke checks against {args.live}")
        return smoke_live(args.live)

    print("Running local smoke checks against handler in this repository")
    return smoke_local(args.idempotency_source, args.idempotency_owner)


if __name__ == "__main__":
    raise SystemExit(main())
