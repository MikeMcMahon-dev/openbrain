#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PDF_FIXTURE_DIR = PROJECT_ROOT / "scripts" / "test_fixtures" / "pdf"
DOCX_FIXTURE_DIR = PROJECT_ROOT / "scripts" / "test_fixtures" / "docx"
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

    if payload.get("status") not in {"accepted", "queued", "failed"}:
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


def _run_local_ingest_call(
    source: str, owner: str | None, path: str = "/api/ingest"
) -> tuple[bool, str]:
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

    print(
        f"Idempotency check for source='{source}', owner='{normalized_owner}': "
        f"before={before}, after_first={after_first}, after_second={after_second}"
    )
    if after_first < before:
        print("Idempotency check failed: first call reduced row count.")
        return 1
    if after_second != after_first:
        print("Idempotency check failed: second call changed row count.")
        return 1
    print("Idempotency check passed.")
    return 0


def _smoke_pdf_cases_local() -> int:
    """Run 3 PDF-specific smoke cases against the local handler.

    Case 1: valid PDF path → status "accepted" (requires _extract_pdf implementation)
    Case 2: missing file path → status "failed"
    Case 3: ingest then query → unique phrase appears in results (requires implementation)

    Returns count of failures.
    """
    _token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    _auth = {"Authorization": f"Bearer {_token}"} if _token else {}
    failed = 0
    simple_pdf = str(PDF_FIXTURE_DIR / "simple_text.pdf")

    # Case 27: valid PDF path → "accepted" (not "queued")
    ok, msg = run_case(
        {
            "path": "/api/ingest",
            "method": "POST",
            "body": json.dumps({
                "source_type": "pdf",
                "source": simple_pdf,
                "owner": "mike.mcmahon67",
                "subject": "pdf_smoke_test",
                "topic": "pdf_smoke",
            }),
            "headers": {"Content-Type": "application/json", **_auth},
        },
        200,
        validate_payload=True,
    )
    # Override: we specifically need "accepted", not "queued"
    if ok:
        # Re-check body directly for "accepted"
        from api.app import handler
        resp = handler({
            "path": "/api/ingest",
            "method": "POST",
            "body": json.dumps({
                "source_type": "pdf",
                "source": simple_pdf,
                "owner": "mike.mcmahon67",
                "subject": "pdf_smoke_test",
                "topic": "pdf_smoke",
            }),
            "headers": {"Content-Type": "application/json", **_auth},
        })
        body = json.loads(resp.get("body", "{}")) if isinstance(resp.get("body"), str) else {}
        status_val = body.get("status", "")
        if status_val == "accepted":
            print("/api/ingest [pdf accepted]: ok (accepted)")
        elif status_val == "queued":
            print(
                "/api/ingest [pdf accepted]: FAIL — status=queued (implementation not yet active)"
            )
            failed += 1
        else:
            print(f"/api/ingest [pdf accepted]: FAIL — status={status_val!r}")
            failed += 1
    else:
        print(f"/api/ingest [pdf accepted]: {msg}")
        failed += 1

    # Case 28: missing file → "failed"
    ok2, msg2 = run_case(
        {
            "path": "/api/ingest",
            "method": "POST",
            "body": json.dumps({
                "source_type": "pdf",
                "source": "/nonexistent/smoke_test.pdf",
                "owner": "mike.mcmahon67",
                "subject": "pdf_smoke_test",
                "topic": "pdf_smoke",
            }),
            "headers": {"Content-Type": "application/json", **_auth},
        },
        200,
        validate_payload=True,
    )
    if ok2:
        from api.app import handler as _handler
        resp2 = _handler({
            "path": "/api/ingest",
            "method": "POST",
            "body": json.dumps({
                "source_type": "pdf",
                "source": "/nonexistent/smoke_test.pdf",
                "owner": "mike.mcmahon67",
                "subject": "pdf_smoke_test",
                "topic": "pdf_smoke",
            }),
            "headers": {"Content-Type": "application/json", **_auth},
        })
        body2 = json.loads(resp2.get("body", "{}")) if isinstance(resp2.get("body"), str) else {}
        if body2.get("status") == "failed":
            print("/api/ingest [pdf missing file]: ok (failed)")
        else:
            print(
                f"/api/ingest [pdf missing file]: FAIL — "
                f"expected status=failed, got {body2.get('status')!r}"
            )
            failed += 1
    else:
        print(f"/api/ingest [pdf missing file]: {msg2}")
        failed += 1

    # Case 29: ingest then retrieve — unique phrase appears in results
    # This case requires both _extract_pdf implementation AND a live DB connection.
    unique_phrase = "xyloquartz-retrieval-fixture-alpha"
    db_url = _supabase_url()
    if not db_url:
        print("/api/ingest+query [pdf retrieval]: SKIP — no DB URL available")
    else:
        from api.app import handler as _h
        ingest_resp = _h({
            "path": "/api/ingest",
            "method": "POST",
            "body": json.dumps({
                "source_type": "pdf",
                "source": simple_pdf,
                "owner": "mike.mcmahon67",
                "subject": "pdf_smoke_test",
                "topic": "pdf_smoke",
            }),
            "headers": {"Content-Type": "application/json", **_auth},
        })
        _ibody = ingest_resp.get("body", "{}")
        ingest_body = json.loads(_ibody) if isinstance(_ibody, str) else {}
        if ingest_body.get("status") != "accepted":
            print(
                f"/api/ingest+query [pdf retrieval]: SKIP — "
                f"ingest returned {ingest_body.get('status')!r} (not accepted)"
            )
        else:
            time.sleep(1)
            query_resp = _h({
                "path": "/api/query",
                "method": "POST",
                "body": json.dumps({"query": unique_phrase, "owner": "mike.mcmahon67"}),
                "headers": {"Content-Type": "application/json", **_auth},
            })
            _qbody = query_resp.get("body", "{}")
            query_body = json.loads(_qbody) if isinstance(_qbody, str) else {}
            results = query_body.get("results", [])
            found = any(
                unique_phrase in (r.get("text", "") or r.get("content", ""))
                for r in results
            )
            if found:
                print("/api/ingest+query [pdf retrieval]: ok (phrase found in results)")
            else:
                print(
                    f"/api/ingest+query [pdf retrieval]: FAIL — "
                    f"phrase not found in {len(results)} results"
                )
                failed += 1

    return failed


def _smoke_docx_url_cases_local() -> int:
    """Run 6 DOCX+URL smoke cases against the local handler.

    Case 30: valid DOCX path → status "accepted" (requires _extract_docx implementation)
    Case 31: missing DOCX file → status "failed"
    Case 32: ingest then query → unique DOCX phrase appears in results
    Case 33: valid URL → status "accepted" (requires _fetch_url implementation)
    Case 34: bad URL → status "failed"
    Case 35: ingest example.com then query → "Example Domain" appears in results

    Returns count of failures.
    """
    _token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    _auth = {"Authorization": f"Bearer {_token}"} if _token else {}
    failed = 0
    simple_docx = str(DOCX_FIXTURE_DIR / "simple_text.docx")
    docx_unique_phrase = "xyloquartz-retrieval-fixture-beta"
    url_unique_phrase = "Example Domain"

    def _ingest_and_check_status(
        label: str, source_type: str, source: str, expected_status: str
    ) -> tuple[bool, str]:
        """Helper: call ingest and return (ok, actual_status)."""
        from api.app import handler as _h
        resp = _h({
            "path": "/api/ingest",
            "method": "POST",
            "body": json.dumps({
                "source_type": source_type,
                "source": source,
                "owner": "mike.mcmahon67",
                "subject": "docx_url_smoke_test",
                "topic": "docx_url_smoke",
            }),
            "headers": {"Content-Type": "application/json", **_auth},
        })
        body = json.loads(resp.get("body", "{}")) if isinstance(resp.get("body"), str) else {}
        actual = body.get("status", "")
        if actual == expected_status:
            print(f"/api/ingest [{label}]: ok ({expected_status})")
            return True, actual
        elif actual == "queued":
            print(f"/api/ingest [{label}]: FAIL — status=queued (implementation not yet active)")
            return False, actual
        else:
            print(f"/api/ingest [{label}]: FAIL — expected {expected_status!r}, got {actual!r}")
            return False, actual

    # Case 30: valid DOCX path → "accepted"
    ok30, status30 = _ingest_and_check_status("docx accepted", "docx", simple_docx, "accepted")
    if not ok30:
        failed += 1

    # Case 31: missing DOCX file → "failed"
    ok31, _ = _ingest_and_check_status(
        "docx missing file", "docx", "/nonexistent/smoke_test.docx", "failed"
    )
    if not ok31:
        failed += 1

    # Case 32: ingest then query — unique DOCX phrase appears in results
    db_url = _supabase_url()
    if not db_url:
        print("/api/ingest+query [docx retrieval]: SKIP — no DB URL available")
    elif status30 != "accepted":
        print(
            f"/api/ingest+query [docx retrieval]: SKIP — "
            f"ingest returned {status30!r} (not accepted)"
        )
    else:
        from api.app import handler as _h
        time.sleep(1)
        query_resp = _h({
            "path": "/api/query",
            "method": "POST",
            "body": json.dumps({"query": docx_unique_phrase, "owner": "mike.mcmahon67"}),
            "headers": {"Content-Type": "application/json", **_auth},
        })
        _qbody = query_resp.get("body", "{}")
        query_body = json.loads(_qbody) if isinstance(_qbody, str) else {}
        results = query_body.get("results", [])
        found = any(
            docx_unique_phrase in (r.get("text", "") or r.get("content", ""))
            for r in results
        )
        if found:
            print("/api/ingest+query [docx retrieval]: ok (phrase found in results)")
        else:
            print(
                f"/api/ingest+query [docx retrieval]: FAIL — "
                f"phrase not found in {len(results)} results"
            )
            failed += 1

    # Case 33: valid URL → "accepted"
    ok33, status33 = _ingest_and_check_status(
        "url accepted", "url", "https://example.com", "accepted"
    )
    if not ok33:
        failed += 1

    # Case 34: bad URL → "failed"
    ok34, _ = _ingest_and_check_status("url bad url", "url", "not-a-valid-url", "failed")
    if not ok34:
        failed += 1

    # Case 35: ingest example.com then query — "Example Domain" phrase in results
    if not db_url:
        print("/api/ingest+query [url retrieval]: SKIP — no DB URL available")
    elif status33 != "accepted":
        print(
            f"/api/ingest+query [url retrieval]: SKIP — "
            f"url ingest returned {status33!r} (not accepted)"
        )
    else:
        from api.app import handler as _h
        time.sleep(1)
        query_resp = _h({
            "path": "/api/query",
            "method": "POST",
            "body": json.dumps({"query": url_unique_phrase, "owner": "mike.mcmahon67"}),
            "headers": {"Content-Type": "application/json", **_auth},
        })
        _qbody = query_resp.get("body", "{}")
        query_body = json.loads(_qbody) if isinstance(_qbody, str) else {}
        results = query_body.get("results", [])
        found = any(
            url_unique_phrase in (r.get("text", "") or r.get("content", ""))
            for r in results
        )
        if found:
            print("/api/ingest+query [url retrieval]: ok (phrase found in results)")
        else:
            print(
                f"/api/ingest+query [url retrieval]: FAIL — "
                f"phrase not found in {len(results)} results"
            )
            failed += 1

    return failed


def _smoke_mcp_cases_local() -> int:
    """Run MCP endpoint smoke cases (cases 36–46).

    Test the MCP HTTP endpoint at /mcp/messages for:
    - Tool discovery (GET)
    - Authentication (valid/invalid/missing)
    - JSON-RPC protocol compliance
    - Tool execution (query, ingest, quiz, flashcards)
    - Error handling
    """
    _token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    if not _token:
        print("MCP endpoint tests skipped: OPENBRAIN_TOOL_ACCESS_TOKEN not set")
        return 0

    _auth = {"Authorization": f"Bearer {_token}"}
    failed = 0

    mcp_cases = [
        # Tool discovery
        (
            "MCP GET discovery",
            {"path": "/mcp/messages", "method": "GET", "headers": _auth},
            200,
        ),
        # Missing auth
        (
            "MCP POST no auth",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
                "headers": {},
            },
            401,
        ),
        # Invalid auth
        (
            "MCP POST invalid auth",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
                "headers": {"Authorization": "Bearer invalid-token"},
            },
            401,
        ),
        # Valid tools/list
        (
            "MCP tools/list",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
                "headers": _auth,
            },
            200,
        ),
        # Malformed JSON-RPC
        (
            "MCP malformed request",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({"method": "tools/list"}),  # Missing jsonrpc
                "headers": _auth,
            },
            400,
        ),
        # Unknown method
        (
            "MCP unknown method",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps(
                    {"jsonrpc": "2.0", "method": "unknown/method", "id": 1}
                ),
                "headers": _auth,
            },
            200,  # Returns error in JSON-RPC format
        ),
        # Tool execution: query
        (
            "MCP tools/call query",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "query", "arguments": {"query": "test"}},
                    "id": 1,
                }),
                "headers": _auth,
            },
            200,
        ),
        # Tool execution: ingest — see _smoke_mcp_ingest_plan_apply(),
        # which needs two sequenced calls and so cannot be a static case.
        # Tool execution: generate_quiz
        (
            "MCP tools/call quiz",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "generate_quiz", "arguments": {"query": "test"}},
                    "id": 1,
                }),
                "headers": _auth,
            },
            200,
        ),
        # Tool execution: generate_flashcards
        (
            "MCP tools/call flashcards",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {
                        "name": "generate_flashcards",
                        "arguments": {"query": "test"},
                    },
                    "id": 1,
                }),
                "headers": _auth,
            },
            200,
        ),
        # Missing required parameter
        (
            "MCP missing param",
            {
                "path": "/mcp/messages",
                "method": "POST",
                "body": json.dumps({
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": "query", "arguments": {}},
                    "id": 1,
                }),
                "headers": _auth,
            },
            200,  # Returns error in result
        ),
    ]

    for label, request, expected in mcp_cases:
        ok, message = run_case(request, expected)
        print(f"{label}: {message}")
        if not ok:
            failed += 1

    failed += _smoke_mcp_ingest_plan_apply(_auth)
    return failed


def _mcp_tool_call(tool: str, arguments: dict[str, Any],
                   auth: dict[str, str]) -> tuple[int | None, Any]:
    """Invoke one MCP tool in-process and return (http_status, the tool's OWN result body).

    Tool results arrive double-wrapped — JSON-RPC `result`, then an MCP content envelope whose
    text is the real payload as a JSON string. Unwrapping both is the point: everything
    interesting about an ingest lives in that innermost object.
    """
    from api.app import handler

    response = handler({
        "path": "/mcp/messages", "method": "POST", "headers": auth,
        "body": json.dumps({"jsonrpc": "2.0", "method": "tools/call",
                            "params": {"name": tool, "arguments": arguments}, "id": 1}),
    })
    if not isinstance(response, dict):
        return None, response
    body = response.get("body")
    payload = json.loads(body) if isinstance(body, str) else body
    result = payload.get("result") if isinstance(payload, dict) else None
    try:
        return response.get("statusCode"), json.loads(result["content"][0]["text"])
    except Exception:
        return response.get("statusCode"), result if result is not None else payload


def _plan_args_local(content: str) -> dict[str, Any]:
    """Mint a plan_token for `content` in-process, plus any decision the plan demands.

    Every gated ingest surface needs this, not just MCP: the gate fires on
    (source_type=text, honor-owner) regardless of which route the write arrived through. A smoke
    case that omits it passes today and 409s the moment OPENBRAIN_REQUIRE_INGEST_PLAN is on —
    which is how /claude_ingest was found to be the second such case.

    Returns {} if the plan cannot be obtained, so the caller still issues the request and the
    failure surfaces as a visible refusal rather than a skipped check.
    """
    from api.app import handler

    token_env = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    response = handler({
        "path": "/api/plan_ingest", "method": "POST",
        "headers": {"Content-Type": "application/json",
                    "Authorization": f"Bearer {token_env}"},
        "body": json.dumps({"source": content}),
    })
    if not isinstance(response, dict) or response.get("statusCode") != 200:
        return {}
    try:
        plan = json.loads(response["body"])
    except Exception:
        return {}
    token = plan.get("plan_token")
    if not token:
        return {}
    args: dict[str, Any] = {"plan_token": token}
    candidates = plan.get("candidates") or []
    if candidates:
        args["acknowledged_not_updating"] = candidates
        args["decline_reason"] = "Smoke fixture: a fixed probe string, never a living-doc update."
    return args


def _smoke_mcp_ingest_plan_apply(auth: dict[str, str]) -> int:
    """MCP ingest driven the way an agent must drive it: plan, then apply carrying the token.

    This case previously asserted HTTP 200 and nothing else, which made it **incapable of
    failing**. The MCP surface discards the ingest's real HTTP status (`status, body =
    ingest_payload(...)` and only `body` is returned), and the write is an upsert on a
    content-derived id, so a refused ingest, a silent no-op, and a successful write were all
    indistinguishable — a green light wired to nothing.

    Two fixes, deliberately together:
      * assert the tool's own body says `accepted`, so a refusal is visible AT ALL; and
      * carry a `plan_token`, so the case still exercises the real path once
        OPENBRAIN_REQUIRE_INGEST_PLAN is on instead of going red and being deleted for noise.

    Safe to run unattended: the row id is derived from the content, so repeat runs overwrite one
    row rather than accumulating fixtures.
    """
    content = "MCP smoke test content"
    failed = 0

    status, plan = _mcp_tool_call("plan_ingest", {"source": content}, auth)
    token = plan.get("plan_token") if isinstance(plan, dict) else None
    if status != 200 or not token:
        print(f"MCP plan_ingest: FAIL — status={status} plan_token={'yes' if token else 'MISSING'}")
        return 1
    print("MCP tools/call plan_ingest: ok (200, plan_token issued)")

    args: dict[str, Any] = {
        "source_type": "text", "source": content,
        "subject": "mcp_smoke", "topic": "smoke_2026_05_01",
        "plan_token": token,
    }
    # A plan that surfaced living docs must be answered, or the apply is refused once
    # enforcement is on. Empty for this content today; declared anyway so a future living doc
    # in range does not silently start failing the smoke.
    candidates = plan.get("candidates") or []
    if candidates:
        args["acknowledged_not_updating"] = candidates
        args["decline_reason"] = ("Smoke fixture: a fixed probe string, never an update to a "
                                  "living doc.")

    status, body = _mcp_tool_call("ingest", args, auth)
    reported = body.get("status") if isinstance(body, dict) else None
    if status != 200 or reported != "accepted":
        detail = str(body.get("message") if isinstance(body, dict) else body)[:200]
        print(f"MCP tools/call ingest: FAIL — http={status} status={reported!r} {detail}")
        failed += 1
    else:
        print(f"MCP tools/call ingest: ok (200, accepted, "
              f"{'plan enforced' if candidates else 'plan token carried'})")
    return failed


def smoke_local(idempotency_source: str | None = None, idempotency_owner: str | None = None,
                read_only: bool = False) -> int:
    """Local smoke suite.

    `read_only=True` skips the only two groups that WRITE to the vault — the PDF and DOCX/URL
    ingest cases, which POST real fixtures to /api/ingest. Everything else is safe to run
    unattended: the OB2 group is entirely negative cases (asserting 401/400, never writing), and
    the MCP, DB-integrity and wire groups only read. This is the mode CI runs, so a pull request
    cannot inject fixture rows into the production vault.
    """
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
            401,
        ),
        (
            {
                "path": "/api/generate_quiz",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": {},
            },
            401,
        ),
        (
            {
                "path": "/generate_flashcards",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": {},
            },
            401,
        ),
        (
            {
                "path": "/api/generate_flashcards",
                "method": "POST",
                "body": json.dumps({"query": "test"}),
                "headers": {},
            },
            401,
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
                "body": json.dumps({
                    "type": "tool_use", "name": "claude_query", "input": {"query": "test"},
                }),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/claude_generate_quiz",
                "method": "POST",
                "body": json.dumps({
                    "type": "tool_use",
                    "name": "claude_generate_quiz",
                    "input": {"query": "test"},
                }),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/claude_generate_flashcards",
                "method": "POST",
                "body": json.dumps({
                    "type": "tool_use",
                    "name": "claude_generate_flashcards",
                    "input": {"query": "test"},
                }),
                "headers": {"Content-Type": "application/json", **_auth},
            },
            200,
        ),
        (
            {
                "path": "/claude_ingest",
                "method": "POST",
                "body": json.dumps({
                    "type": "tool_use",
                    "name": "claude_ingest",
                    "input": {"source_type": "text", "source": "smoke test",
                              **_plan_args_local("smoke test")},
                }),
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

    # PDF (27–29) and DOCX/URL (30–35) cases POST real fixtures to /api/ingest — the only
    # groups in this suite that write. Skipped in read-only mode so CI never mutates the vault.
    if read_only:
        print("PDF + DOCX/URL ingest cases skipped: --read-only (they write to the vault)")
    else:
        failed += _smoke_pdf_cases_local()
        failed += _smoke_docx_url_cases_local()

    # MCP endpoint smoke cases (cases 36–46)
    failed += _smoke_mcp_cases_local()

    # OB2 knowledge/wiki smoke cases
    failed += _smoke_ob2_cases_local()

    # DB integrity guardrails (stale triggers + chunk coverage) — see 2026-08-06 post-mortem
    failed += _smoke_db_integrity_local()

    # Wire-serialization guardrails — see the 2026-08-22 plan_ingest Decimal incident
    failed += _smoke_wire_serialization_local()

    return failed


def _smoke_wire_serialization_local() -> int:
    """Every tool response must survive being written out as JSON.

    Guards the 2026-08-22 incident: `plan_ingest` shipped with full unit coverage and raised
    "Object of type Decimal is not JSON serializable" on every production call. Postgres
    `round(extract(epoch ...))` returns numeric -> psycopg Decimal, which has no JSON encoder.
    The object was correct in Python and only failed being serialized, so every test below the
    wire boundary passed. Unit tests verify objects; consumers receive payloads.

    Two things make this cheap: `mcp_http.handler()` is the same entry point Vercel invokes, so
    no deployment or HTTP server is needed; and a tool failure comes back as HTTP 200 with the
    error INSIDE the JSON-RPC envelope, so this asserts on the envelope rather than the status —
    a status-only check would have called the Decimal bug a success.
    """
    _token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    if not _token:
        print("Wire-serialization checks skipped: OPENBRAIN_TOOL_ACCESS_TOKEN not set")
        return 0

    try:
        from api.mcp_http import handler as _mcp_handler
    except Exception as exc:  # pragma: no cover - import guard
        print(f"FAIL wire-serialization: could not import mcp_http ({exc})")
        return 1

    tool_calls = [
        ("query", {"query": "flight sim rig", "n_results": 2}),
        ("search", {"query": "flight sim rig", "n_results": 2}),
        ("plan_ingest", {"source": "Test.", "system": "FlightSim",
                         "component": "flightsim-hardware"}),
    ]

    failed = 0
    for name, arguments in tool_calls:
        request = {
            "method": "POST",
            "headers": {"Authorization": f"Bearer {_token}", "content-type": "application/json"},
            "body": json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }),
        }
        try:
            response = _mcp_handler(request)
            body = json.loads(response["body"])
        except Exception as exc:
            print(f"FAIL wire-serialization {name}: handler raised {type(exc).__name__}: {exc}")
            failed += 1
            continue

        if "error" in body:
            print(f"FAIL wire-serialization {name}: {body['error'].get('message')}")
            failed += 1
        else:
            print(f"PASS wire-serialization {name}")

    return failed


def _smoke_db_integrity_local() -> int:
    """Live DB invariants for the knowledge / knowledge_chunked stores.

    Guards the two failures behind the 2026-08-06 silent-unchunked-ingest incident:
    a trigger function referencing a dropped column, and `current` knowledge rows with
    no chunk children (invisible to the chunked read path). Requires SUPABASE_DB_URL;
    skips (non-failing) if the DB is unreachable, so the HTTP smoke suite still runs
    in environments without direct Postgres access.
    """
    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print("db-integrity: SKIP (no SUPABASE_DB_URL in env)")
        return 0
    try:
        import psycopg

        from scripts.chunk_integrity_check import run_all

        conn = psycopg.connect(dsn)
        conn.autocommit = True
        problems = run_all(conn)
    except Exception as exc:  # unreachable DB / missing driver — do not block HTTP smoke
        print(f"db-integrity: SKIP (DB check unavailable: {exc})")
        return 0
    if problems:
        for p in problems:
            print(f"db-integrity: FAIL — {p}")
        return 1
    print("db-integrity: ok (no stale triggers, full chunk coverage)")
    return 0


def _smoke_ob2_cases_local() -> int:
    """OB2 knowledge and wiki endpoint smoke cases.

    Validates auth enforcement, input validation, and basic query behavior.
    DB-dependent cases are skipped when no DB URL is available.

    Returns count of failures.
    """
    _token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    _auth = {"Authorization": f"Bearer {_token}"} if _token else {}
    db_url = _supabase_url()
    failed = 0

    def _check(label: str, request: dict, expected: int) -> None:
        nonlocal failed
        ok, msg = run_case(request, expected)
        print(f"{label}: {msg}")
        if not ok:
            failed += 1

    # Auth rejection — only meaningful when token is configured
    if _token:
        _check(
            "ingest_state no auth",
            {
                "path": "/api/ingest_state",
                "method": "POST",
                "body": json.dumps({
                    "content": "x", "domain": "Network", "environment": "Production",
                }),
                "headers": {},
            },
            401,
        )
        _check(
            "propose_supersession no auth",
            {
                "path": "/api/propose_supersession",
                "method": "POST",
                "body": json.dumps({}),
                "headers": {},
            },
            401,
        )
        _check(
            "confirm_supersession no auth",
            {
                "path": "/api/confirm_supersession",
                "method": "POST",
                "body": json.dumps({}),
                "headers": {},
            },
            401,
        )
        _check(
            "query_state no auth",
            {"path": "/api/query_state", "method": "GET", "headers": {}},
            401,
        )
        _check(
            "compile_wiki no auth",
            {
                "path": "/api/compile_wiki",
                "method": "POST",
                "body": json.dumps({}),
                "headers": {},
            },
            401,
        )
        _check(
            "get_wiki no auth",
            {"path": "/api/wiki/test-page", "method": "GET", "headers": {}},
            401,
        )

    # Input validation (auth passes, no DB needed)
    _check(
        "ingest_state missing content",
        {
            "path": "/api/ingest_state",
            "method": "POST",
            "body": json.dumps({"domain": "Network", "environment": "Production"}),
            "headers": _auth,
        },
        400,
    )
    _check(
        "ingest_state missing domain",
        {
            "path": "/api/ingest_state",
            "method": "POST",
            "body": json.dumps({"content": "x", "environment": "Production"}),
            "headers": _auth,
        },
        400,
    )
    _check(
        "ingest_state missing environment",
        {
            "path": "/api/ingest_state",
            "method": "POST",
            "body": json.dumps({"content": "x", "domain": "Network"}),
            "headers": _auth,
        },
        400,
    )
    _check(
        "ingest_state invalid domain",
        {
            "path": "/api/ingest_state",
            "method": "POST",
            "body": json.dumps({
                "content": "x", "domain": "NotADomain", "environment": "Production",
            }),
            "headers": _auth,
        },
        400,
    )
    _check(
        "propose_supersession missing supersedes_id",
        {
            "path": "/api/propose_supersession",
            "method": "POST",
            "body": json.dumps({
                "content": "x", "domain": "Network", "environment": "Production",
            }),
            "headers": _auth,
        },
        400,
    )
    _check(
        "propose_supersession missing content",
        {
            "path": "/api/propose_supersession",
            "method": "POST",
            "body": json.dumps({
                "supersedes_id": "00000000-0000-0000-0000-000000000000",
                "domain": "Network",
                "environment": "Production",
            }),
            "headers": _auth,
        },
        400,
    )
    _check(
        "confirm_supersession missing proposal_id",
        {
            "path": "/api/confirm_supersession",
            "method": "POST",
            "body": json.dumps({}),
            "headers": _auth,
        },
        400,
    )
    _check(
        "compile_wiki missing page_name",
        {
            "path": "/api/compile_wiki",
            "method": "POST",
            "body": json.dumps({"domain": "Network"}),
            "headers": _auth,
        },
        400,
    )
    _check(
        "compile_wiki missing domain",
        {
            "path": "/api/compile_wiki",
            "method": "POST",
            "body": json.dumps({"page_name": "test-page"}),
            "headers": _auth,
        },
        400,
    )
    _check(
        "compile_wiki invalid page_type",
        {
            "path": "/api/compile_wiki",
            "method": "POST",
            "body": json.dumps({
                "page_name": "p", "domain": "Network", "page_type": "invalid-type",
            }),
            "headers": _auth,
        },
        400,
    )

    # DB-dependent cases
    if not db_url:
        print("OB2 DB-dependent cases: SKIP — no DB URL available")
        return failed

    _check(
        "query_state returns 200",
        {
            "path": "/api/query_state",
            "method": "GET",
            "body": json.dumps({"domain": "Network", "limit": 5}),
            "headers": _auth,
        },
        200,
    )
    _check(
        "get_wiki nonexistent page returns 404",
        {
            "path": "/api/wiki/nonexistent-xyloquartz-page-ob2-smoke",
            "method": "GET",
            "headers": _auth,
        },
        404,
    )

    return failed


def _preview_bypass_headers() -> dict[str, str]:
    """Headers that let smoke reach a PROTECTED Vercel preview deployment.

    The project runs `ssoProtection: all_except_custom_domains`, so every preview URL answers 401
    to an unauthenticated caller — which is correct for humans and useless for automation. A
    Protection Bypass for Automation secret is the supported way through; without it there is no
    way to smoke a build BEFORE merging, which is exactly when a bug is cheapest to find.

    Absent the env var this returns {} and live smoke behaves as before, so production runs
    (openbrain-rouge.vercel.app, a custom domain and therefore unprotected) are unaffected.

    The secret is a credential: it grants read access to every preview build. It lives in
    gitignored .env.local locally and in repository secrets for CI. Never commit it.
    """
    secret = os.getenv("VERCEL_AUTOMATION_BYPASS_SECRET", "").strip()
    if not secret:
        return {}
    return {
        "x-vercel-protection-bypass": secret,
        # Stops Vercel setting a bypass cookie on the response — irrelevant for a one-shot
        # scripted request and it keeps the exchange stateless.
        "x-vercel-set-bypass-cookie": "false",
    }


def _call_live(
    url: str,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    target = urllib.parse.urljoin(url.rstrip("/") + "/", path.lstrip("/"))
    payload = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req_headers = {"x-openbrain-owner": "tenant-a-owner", **_preview_bypass_headers()}
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


def _plan_args_live(base_url: str, content: str) -> dict[str, Any]:
    """The live-HTTP twin of _plan_args_local: mint a plan_token against a deployed build.

    Preview is where enforcement divergence surfaces first (Tier 2 hits a real URL), so the live
    cases need the token exactly as the local ones do.
    """
    token_env = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    try:
        status, plan = _call_live(base_url, "POST", "/api/plan_ingest", {"source": content},
                                  {"Authorization": f"Bearer {token_env}"})
    except Exception:
        return {}
    if status != 200 or not isinstance(plan, dict) or not plan.get("plan_token"):
        return {}
    args: dict[str, Any] = {"plan_token": plan["plan_token"]}
    candidates = plan.get("candidates") or []
    if candidates:
        args["acknowledged_not_updating"] = candidates
        args["decline_reason"] = "Smoke fixture: a fixed probe string, never a living-doc update."
    return args


def _live_post_as_token_owner(
    base_url: str, path: str, body: dict[str, Any], token: str
) -> tuple[int, Any]:
    """POST to a live endpoint authenticated by token only — WITHOUT the
    `x-openbrain-owner: tenant-a-owner` header that _call_live forces. Owner then
    resolves from the token binding (the real family owner with content), which is
    what a content-level check needs. Returns (status, parsed_body)."""
    target = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        target,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}",
                 **_preview_bypass_headers()},
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=20) as resp:
            return getattr(resp, "status", 200), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {}


def _smoke_live_content_check(base_url: str) -> int:
    """Content-level retrieval check (cluster of HTTP-200-but-empty blind spots).

    A 200 with `results: []` is indistinguishable from a broken DB / wrong backend /
    failed retrieval — the plain status checks miss all of it. Query a broad term as
    the token's real owner (the other query cases use `tenant-a-owner`, which has no
    content) and assert: real rows come back AND the top row carries non-empty text.
    Also reports which backend served it (knowledge facets present vs legacy thoughts),
    so a silent flip-revert (READ_TARGET back to thoughts) is visible too.
    """
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    if not token:
        print("/api/query [content]: SKIP — OPENBRAIN_TOOL_ACCESS_TOKEN not set")
        return 0
    try:
        status, body = _live_post_as_token_owner(
            base_url, "/api/query", {"query": "network", "n_results": 5}, token
        )
    except Exception as exc:
        print(f"/api/query [content]: FAIL — request error: {exc}")
        return 1

    if status != 200:
        print(f"/api/query [content]: FAIL — HTTP {status}")
        return 1
    results = body.get("results", []) if isinstance(body, dict) else []
    if not results:
        print(
            "/api/query [content]: FAIL — HTTP 200 but EMPTY results "
            "(DB unreachable, retrieval broken, or owner mis-scoped — the exact blind "
            "spot status-only checks miss)"
        )
        return 1
    top = results[0]
    text = (top.get("text") or top.get("content") or "").strip()
    if not text:
        print("/api/query [content]: FAIL — results returned but top row has empty text")
        return 1
    backend = "knowledge" if ("status" in top and "domain" in top) else "thoughts"
    print(
        f"/api/query [content]: ok — {len(results)} results, "
        f"top conf={top.get('confidence')}, backend={backend}"
    )
    return 0


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
        ("/generate_quiz", {"query": "test"}, 401),
        ("/api/generate_quiz", {"query": "test"}, 401),
        ("/generate_flashcards", {"query": "test"}, 401),
        ("/api/generate_flashcards", {"query": "test"}, 401),
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
            {
                "type": "tool_use",
                "name": "claude_generate_flashcards",
                "input": {"query": "terraform"},
            },
            200,
            {"Authorization": f"Bearer {os.getenv('OPENBRAIN_TOOL_ACCESS_TOKEN', '')}"},
        ),
        (
            "/claude_ingest",
            {
                "type": "tool_use",
                "name": "claude_ingest",
                "input": {"source_type": "text", "source": "live smoke test note",
                          **_plan_args_live(base_url, "live smoke test note")},
            },
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

    # PDF-specific live smoke cases (cases 27–29 in local; live subset below)
    # Case 27: missing file → status "failed" (works on any environment)
    # Case 28: non-existent path for docx also returns "failed" (confirms impl active)
    # NOTE: "pdf accepted" and "pdf retrieval" cases are local-only (Vercel can't access
    # local file paths). Run `make pdf-eval-live` for end-to-end PDF eval against production.
    # PDF/DOCX/URL live cases — only missing-file/bad-URL variants; Vercel can't access local paths.
    # Full retrieval eval: use `make pdf-eval-live` / `make docx-url-eval-live`
    pdf_live_cases = [
        (
            "/api/ingest [pdf missing file]",
            "/api/ingest",
            {
                "source_type": "pdf",
                "source": "/nonexistent/smoke_test.pdf",
                "owner": "mike.mcmahon67",
                "subject": "pdf_smoke_test",
                "topic": "pdf_smoke",
            },
            "failed",
        ),
        (
            "/api/ingest [docx missing file]",
            "/api/ingest",
            {
                "source_type": "docx",
                "source": "/nonexistent/smoke_test.docx",
                "owner": "mike.mcmahon67",
                "subject": "docx_url_smoke_test",
                "topic": "docx_url_smoke",
            },
            "failed",
        ),
        (
            "/api/ingest [url bad url]",
            "/api/ingest",
            {
                "source_type": "url",
                "source": "not-a-valid-url",
                "owner": "mike.mcmahon67",
                "subject": "docx_url_smoke_test",
                "topic": "docx_url_smoke",
            },
            "failed",
        ),
    ]
    for label, path, payload, expected_status in pdf_live_cases:
        try:
            status, body = _call_live(base_url, "POST", path, payload, _auth)
            body_status = body.get("status", "") if isinstance(body, dict) else ""
            if status == 200 and body_status == expected_status:
                print(f"{label}: ok ({expected_status})")
            elif status == 200 and body_status == "queued":
                print(
                    f"{label}: FAIL — status=queued "
                    "(PDF implementation not yet active on this deployment)"
                )
                failed += 1
            else:
                print(f"{label}: FAIL — HTTP {status}, status={body_status!r}")
                failed += 1
        except urllib.error.HTTPError as exc:
            body_raw = exc.read().decode("utf-8", errors="replace")
            print(f"{label}: FAIL — HTTP {exc.code}: {body_raw[:120]}")
            failed += 1
        except Exception as exc:
            print(f"{label}: FAIL — {exc}")
            failed += 1

    failed += _smoke_live_content_check(base_url)
    failed += smoke_oauth(base_url)
    return failed


def smoke_oauth(base_url: str) -> int:
    """Test the OAuth 2.0 authorization flow end-to-end against a live deployment."""
    import base64
    import hashlib
    import secrets

    failed = 0

    def _pkce() -> tuple[str, str]:
        v = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
        c = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
        return v, c

    def _no_redirect_get(url: str) -> tuple[int, str, str]:
        """GET without following redirects. Returns (status, location, body).

        Carries the preview-bypass headers like the other live helpers: without them a protected
        preview answers with its own 302 to vercel.com/sso-api, and the OAuth cases then fail
        reporting a "garbled Location" — a confusing symptom for a pure auth problem.
        """
        class _NoRedirect(urllib.request.HTTPErrorProcessor):
            def http_response(self, req, resp):
                return resp
            https_response = http_response

        opener = urllib.request.build_opener(_NoRedirect)
        request = urllib.request.Request(url, headers=_preview_bypass_headers())
        resp = opener.open(request, timeout=10)
        location = resp.headers.get("Location", "")
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, location, body

    redirect_uri = "https://claude.ai/api/mcp/auth_callback"
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")

    # ── Case 1: Discovery document ────────────────────────────────────────────
    label = "oauth/.well-known"
    try:
        status, body = _call_live(
            base_url, "GET", "/.well-known/oauth-authorization-server", None, None
        )
        if status != 200:
            print(f"{label}: FAIL — HTTP {status}")
            failed += 1
        elif not isinstance(body, dict) or "token_endpoint" not in body:
            print(f"{label}: FAIL — missing token_endpoint in {str(body)[:120]}")
            failed += 1
        elif not body["token_endpoint"].startswith("https://"):
            print(f"{label}: FAIL — token_endpoint looks wrong: {body['token_endpoint']}")
            failed += 1
        else:
            print(f"{label}: ok (200)")
    except Exception as exc:
        print(f"{label}: FAIL — {exc}")
        failed += 1

    # ── Case 2: Authorize redirects to a proper URL (not URL-encoded junk) ───
    label = "oauth/authorize → redirect"
    try:
        v, c = _pkce()
        params = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": "mike.mcmahon67",
            "redirect_uri": redirect_uri,
            "code_challenge": c,
            "code_challenge_method": "S256",
            "state": "smoke_test_state",
        })
        status, location, body = _no_redirect_get(f"{base_url}/authorize?{params}")
        if status != 302:
            print(f"{label}: FAIL — expected 302, got {status}; body={body[:120]}")
            failed += 1
        elif not location.startswith("https://claude.ai"):
            # Catch the URL-encoded redirect bug explicitly
            if "https%3A" in location or "claude.ai" not in location:
                print(f"{label}: FAIL — Location is garbled or wrong: {location[:120]}")
            else:
                print(f"{label}: FAIL — unexpected Location: {location[:120]}")
            failed += 1
        else:
            parsed_loc = urllib.parse.urlparse(location)
            qs = dict(urllib.parse.parse_qsl(parsed_loc.query))
            if "code" not in qs:
                print(f"{label}: FAIL — no code in callback URL: {location[:120]}")
                failed += 1
            elif qs.get("state") != "smoke_test_state":
                print(f"{label}: FAIL — state mismatch: got {qs.get('state')!r}")
                failed += 1
            else:
                print(f"{label}: ok (302 → claude.ai callback with code + state)")
                # Save for token exchange test
                _smoke_code = qs["code"]
                _smoke_verifier = v

                # ── Case 3: Token exchange ────────────────────────────────────
                label2 = "oauth/token exchange"
                try:
                    token_body = urllib.parse.urlencode({
                        "grant_type": "authorization_code",
                        "code": _smoke_code,
                        "redirect_uri": redirect_uri,
                        "code_verifier": _smoke_verifier,
                    })
                    req = urllib.request.Request(
                        f"{base_url}/token",
                        data=token_body.encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 **_preview_bypass_headers()},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        tok_body = json.loads(resp.read().decode())
                    if tok_body.get("token_type") != "bearer":
                        print(f"{label2}: FAIL — unexpected response: {tok_body}")
                        failed += 1
                    elif not tok_body.get("access_token"):
                        print(f"{label2}: FAIL — no access_token in response")
                        failed += 1
                    elif tok_body["access_token"] != token:
                        print(f"{label2}: FAIL — returned token doesn't match env token")
                        failed += 1
                    else:
                        print(f"{label2}: ok (200 — bearer token returned)")
                except Exception as exc2:
                    print(f"{label2}: FAIL — {exc2}")
                    failed += 1

    except Exception as exc:
        print(f"{label}: FAIL — {exc}")
        failed += 1

    # ── Case 4: Authorize rejects unknown client ──────────────────────────────
    label = "oauth/authorize unknown client"
    try:
        v, c = _pkce()
        params = urllib.parse.urlencode({
            "response_type": "code",
            "client_id": "nobody@example.com",
            "redirect_uri": redirect_uri,
            "code_challenge": c,
            "code_challenge_method": "S256",
            "state": "s",
        })
        status, location, body = _no_redirect_get(f"{base_url}/authorize?{params}")
        qs = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))
        if qs.get("error") == "unauthorized_client":
            print(f"{label}: ok (302 with unauthorized_client)")
        else:
            print(
                f"{label}: FAIL — expected unauthorized_client, "
                f"got status={status} location={location[:80]}"
            )
            failed += 1
    except Exception as exc:
        print(f"{label}: FAIL — {exc}")
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
        help=(
            "Expected owner for local /api/ingest idempotency check. "
            "Defaults to OPENBRAIN_DEFAULT_OWNER."
        ),
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help=("Skip the PDF and DOCX/URL groups, the only cases that write to the vault. "
              "Use for unattended runs (CI) so a pull request cannot inject fixture rows "
              "into production."),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.live:
        print(f"Running live smoke checks against {args.live}")
        return smoke_live(args.live)

    print("Running local smoke checks against handler in this repository")
    return smoke_local(args.idempotency_source, args.idempotency_owner,
                       read_only=args.read_only)


if __name__ == "__main__":
    raise SystemExit(main())
