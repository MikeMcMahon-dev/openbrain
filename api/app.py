from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

API_DIR = Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

_HANDLERS: dict[str, Callable] | None = None
_STARTUP_ERROR: str | None = None
DEBUG_ERRORS = os.getenv("OPENBRAIN_DEBUG", "").lower() in {"1", "true", "yes"}


def _bootstrap_handlers() -> None:
    global _HANDLERS, _STARTUP_ERROR
    if _HANDLERS is not None or _STARTUP_ERROR is not None:
        return

    try:
        from _openbrain_api import response_payload
        from generate_flashcards import handler as flashcards_handler
        from generate_quiz import handler as quiz_handler
        from health import handler as health_handler
        from ingest import handler as ingest_handler
        from query import handler as query_handler
        from search import handler as search_handler

        _HANDLERS = {
            "health_handler": health_handler,
            "search_handler": search_handler,
            "query_handler": query_handler,
            "quiz_handler": quiz_handler,
            "flashcards_handler": flashcards_handler,
            "ingest_handler": ingest_handler,
            "response_payload": response_payload,
        }
    except Exception:
        _STARTUP_ERROR = traceback.format_exc()


def _response_payload(status: int, payload: object) -> dict[str, object]:
    if _HANDLERS and _HANDLERS.get("response_payload"):
        return _HANDLERS["response_payload"](status, payload)
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        },
        "body": json.dumps(payload),
    }


def _startup_error_response() -> dict[str, object]:
    if not DEBUG_ERRORS:
        return _response_payload(
            500,
            {
                "error": "function_startup_failed",
                "message": "function handler failed to initialize",
            },
        )
    return _response_payload(
        500,
        {
            "error": "function_startup_failed",
            "message": "function handler failed to initialize",
            "details": _STARTUP_ERROR,
        },
    )


def _extract_path(request) -> str:
    if isinstance(request, dict):
        raw_path = request.get("path")
        if isinstance(raw_path, str):
            return raw_path

        raw_url = request.get("url")
        if isinstance(raw_url, str):
            return urlparse(raw_url).path

    if hasattr(request, "path"):
        value = getattr(request, "path")
        if isinstance(value, str):
            return value

    if hasattr(request, "url"):
        value = getattr(request, "url")
        if isinstance(value, str):
            return urlparse(value).path

    return "/"


def handler(request):
    _bootstrap_handlers()
    path = _extract_path(request)
    if path in {"/", "/health", "/api/health"}:
        return _response_payload(
            200,
            {"status": "openbrain route gateway online"},
        )

    if _STARTUP_ERROR is not None:
        return _startup_error_response()

    response_payload = _HANDLERS["response_payload"]
    search_handler = _HANDLERS["search_handler"]
    query_handler = _HANDLERS["query_handler"]
    quiz_handler = _HANDLERS["quiz_handler"]
    flashcards_handler = _HANDLERS["flashcards_handler"]
    ingest_handler = _HANDLERS["ingest_handler"]

    if path in {"/search", "/api/search"}:
        return search_handler(request)
    if path in {"/query", "/api/query"}:
        return query_handler(request)
    if path in {"/generate_quiz", "/api/generate_quiz"}:
        return quiz_handler(request)
    if path in {"/generate_flashcards", "/api/generate_flashcards"}:
        return flashcards_handler(request)
    if path in {"/ingest", "/api/ingest"}:
        return ingest_handler(request)

    return response_payload(
        404,
        {
            "error": "not_found",
            "message": f"No route configured for {path}",
            "routes": [
                "/health",
                "/api/health",
                "/search",
                "/api/search",
                "/query",
                "/api/query",
                "/generate_quiz",
                "/api/generate_quiz",
                "/generate_flashcards",
                "/api/generate_flashcards",
                "/ingest",
                "/api/ingest",
            ],
        },
    )
