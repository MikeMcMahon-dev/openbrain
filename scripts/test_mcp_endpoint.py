#!/usr/bin/env python3
"""MCP endpoint error handling and validation tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _read_runtime_env() -> None:
    """Load .env for testing."""
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


def run_case(request: dict) -> tuple[int, dict]:
    """Run a test case against the handler."""
    try:
        from api.app import handler
    except Exception as exc:
        raise RuntimeError(f"Failed to import handler: {exc}")

    response = handler(request)
    status = response.get("statusCode", 500)
    body = response.get("body", "{}")

    if isinstance(body, str):
        try:
            body_payload = json.loads(body)
        except json.JSONDecodeError:
            body_payload = {"error": "invalid_json", "raw": body}
    else:
        body_payload = body

    return status, body_payload


def test_mcp_discovery() -> tuple[bool, str]:
    """Test MCP tool discovery (GET /mcp/messages)."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "GET",
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"GET /mcp/messages: expected 200, got {status}"

    tools = body.get("tools", [])
    if len(tools) != 4:
        return False, f"Expected 4 tools, got {len(tools)}"

    tool_names = {t.get("name") for t in tools}
    expected = {"query", "ingest", "generate_quiz", "generate_flashcards"}
    if tool_names != expected:
        return False, f"Tool names mismatch: {tool_names} vs {expected}"

    return True, "GET /mcp/messages: ok (discovery)"


def test_mcp_invalid_auth() -> tuple[bool, str]:
    """Test MCP with invalid authentication."""
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
        "headers": {"Authorization": "Bearer invalid-token"},
    })

    if status != 401:
        return False, f"Expected 401, got {status}"

    if body.get("error") != "unauthorized":
        return False, f"Expected 'unauthorized' error, got {body.get('error')}"

    return True, "POST /mcp/messages (invalid auth): ok (401)"


def test_mcp_missing_auth() -> tuple[bool, str]:
    """Test MCP with missing authentication."""
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
        "headers": {},
    })

    if status != 401:
        return False, f"Expected 401, got {status}"

    return True, "POST /mcp/messages (no auth): ok (401)"


def test_mcp_tools_list() -> tuple[bool, str]:
    """Test MCP tools/list method."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}),
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"Expected 200, got {status}"

    if body.get("jsonrpc") != "2.0":
        return False, f"Expected jsonrpc 2.0, got {body.get('jsonrpc')}"

    result = body.get("result", {})
    tools = result.get("tools", [])
    if len(tools) != 4:
        return False, f"Expected 4 tools in result, got {len(tools)}"

    return True, "POST /mcp/messages (tools/list): ok"


def test_mcp_malformed_jsonrpc() -> tuple[bool, str]:
    """Test MCP with malformed JSON-RPC request."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({"method": "tools/list"}),  # Missing jsonrpc version
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 400:
        return False, f"Expected 400 for malformed request, got {status}"

    if body.get("error") != "invalid_request":
        return False, f"Expected 'invalid_request' error, got {body.get('error')}"

    return True, "POST /mcp/messages (malformed): ok (400)"


def test_mcp_unknown_method() -> tuple[bool, str]:
    """Test MCP with unknown method."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({"jsonrpc": "2.0", "method": "unknown/method", "id": 1}),
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"Expected 200, got {status}"

    error = body.get("error", {})
    if error.get("code") != -32601:
        return False, f"Expected code -32601 (unknown method), got {error.get('code')}"

    return True, "POST /mcp/messages (unknown method): ok (error response)"


def test_mcp_query_tool() -> tuple[bool, str]:
    """Test MCP query tool call."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "query", "arguments": {"query": "test"}},
            "id": 1,
        }),
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"Expected 200, got {status}"

    result = body.get("result", {})
    if "question" not in result:
        return False, f"Expected 'question' in result, got keys: {list(result.keys())}"

    return True, "POST /mcp/messages (tools/call query): ok"


def test_mcp_ingest_tool() -> tuple[bool, str]:
    """Test MCP ingest tool call."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "ingest",
                "arguments": {
                    "source_type": "text",
                    "source": "MCP test content for smoke testing",
                    "subject": "mcp_smoke_test",
                    "topic": "smoke_2026_05_01",
                },
            },
            "id": 1,
        }),
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"Expected 200, got {status}"

    result = body.get("result", {})
    if "ingest_id" not in result:
        return False, f"Expected 'ingest_id' in result, got keys: {list(result.keys())}"

    if result.get("status") not in {"accepted", "queued", "failed"}:
        return False, f"Unexpected ingest status: {result.get('status')}"

    return True, "POST /mcp/messages (tools/call ingest): ok"


def test_mcp_quiz_tool() -> tuple[bool, str]:
    """Test MCP generate_quiz tool call."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "generate_quiz", "arguments": {"query": "photosynthesis"}},
            "id": 1,
        }),
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"Expected 200, got {status}"

    result = body.get("result", {})
    if result.get("mode") != "quiz":
        return False, f"Expected mode='quiz', got {result.get('mode')}"

    return True, "POST /mcp/messages (tools/call quiz): ok"


def test_mcp_flashcards_tool() -> tuple[bool, str]:
    """Test MCP generate_flashcards tool call."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "generate_flashcards", "arguments": {"query": "biology"}},
            "id": 1,
        }),
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"Expected 200, got {status}"

    result = body.get("result", {})
    if result.get("mode") != "flashcards":
        return False, f"Expected mode='flashcards', got {result.get('mode')}"

    return True, "POST /mcp/messages (tools/call flashcards): ok"


def test_mcp_missing_required_param() -> tuple[bool, str]:
    """Test MCP with missing required parameter."""
    token = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
    status, body = run_case({
        "path": "/mcp/messages",
        "method": "POST",
        "body": json.dumps({
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": "query", "arguments": {}},  # Missing required 'query'
            "id": 1,
        }),
        "headers": {"Authorization": f"Bearer {token}"},
    })

    if status != 200:
        return False, f"Expected 200 with error response, got {status}"

    # Should return result with error details (valid JSON-RPC response with error in result)
    result = body.get("result", {})
    if result.get("error") != "validation_error":
        return False, f"Expected validation_error in result, got {result}"

    return True, "POST /mcp/messages (missing param): ok (validation error)"


def main() -> int:
    """Run all MCP endpoint tests."""
    tests = [
        test_mcp_discovery,
        test_mcp_invalid_auth,
        test_mcp_missing_auth,
        test_mcp_tools_list,
        test_mcp_malformed_jsonrpc,
        test_mcp_unknown_method,
        test_mcp_query_tool,
        test_mcp_ingest_tool,
        test_mcp_quiz_tool,
        test_mcp_flashcards_tool,
        test_mcp_missing_required_param,
    ]

    print("=" * 70)
    print("MCP Endpoint Error Handling Tests")
    print("=" * 70)

    failed = 0
    for test in tests:
        try:
            ok, message = test()
            print(message)
            if not ok:
                failed += 1
        except Exception as e:
            print(f"{test.__name__}: FAIL — {e}")
            failed += 1

    print("=" * 70)
    if failed == 0:
        print(f"✅ All {len(tests)} tests passed!")
        return 0
    else:
        print(f"❌ {failed}/{len(tests)} tests failed")
        return failed


if __name__ == "__main__":
    sys.exit(main())
