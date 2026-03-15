#!/usr/bin/env python3
from __future__ import annotations

import argparse
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


def smoke_local() -> int:
    query_body = json.dumps({"query": "test"})
    cases = [
        ({"path": "/", "method": "GET"}, 200),
        ({"path": "/health", "method": "GET"}, 200),
        ({"path": "/api/health", "method": "GET"}, 200),
        (
            {
                "path": "/query",
                "method": "POST",
                "body": query_body,
                "headers": {},
            },
            200,
        ),
        (
            {
                "path": "/api/query",
                "method": "POST",
                "body": query_body,
                "headers": {},
            },
            200,
        ),
        (
            {
                "path": "/search",
                "method": "POST",
                "body": query_body,
                "headers": {},
            },
            200,
        ),
        (
            {
                "path": "/api/search",
                "method": "POST",
                "body": query_body,
                "headers": {},
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
                "headers": {"Content-Type": "application/json"},
            },
            200,
        ),
        (
            {
                "path": "/api/ingest",
                "method": "POST",
                "body": json.dumps({"source_type": "obsidian", "source": "/tmp"}),
                "headers": {"Content-Type": "application/json"},
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
                },
            },
            200,
            True,
            "tenant-a-owner",
        ),
        ({"path": "/bogus-path", "method": "GET"}, 404),
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
    cases = [
        ("/", None, 200),
        ("/health", None, 200),
        ("/api/health", None, 200),
        ("/query", {"query": "test"}, 200),
        ("/api/query", {"query": "test"}, 200),
        ("/search", {"query": "test"}, 200),
        ("/api/search", {"query": "test"}, 200),
        ("/generate_quiz", {"query": "test"}, 200),
        ("/api/generate_quiz", {"query": "test"}, 200),
        ("/generate_flashcards", {"query": "test"}, 200),
        ("/api/generate_flashcards", {"query": "test"}, 200),
        ("/ingest", {"source_type": "obsidian", "source": "/tmp"}, 200),
        (
            "/api/ingest",
            {"source_type": "obsidian", "source": "/tmp", "owner": "evil-user"},
            200,
            {"x-openbrain-owner": "tenant-a-owner"},
            "tenant-a-owner",
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
            ok = False
            message = f"{path}: HTTPError {exc.code} body={body[:320]}"
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.live:
        print(f"Running live smoke checks against {args.live}")
        return smoke_live(args.live)

    print("Running local smoke checks against handler in this repository")
    return smoke_local()


if __name__ == "__main__":
    raise SystemExit(main())
