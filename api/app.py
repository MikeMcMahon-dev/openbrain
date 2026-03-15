from __future__ import annotations

import sys
from pathlib import Path

_API_DIR = Path(__file__).resolve().parent
if str(_API_DIR) not in sys.path:
    sys.path.append(str(_API_DIR))

from health import handler as health_handler
from generate_flashcards import handler as flashcards_handler
from generate_quiz import handler as quiz_handler
from ingest import handler as ingest_handler
from query import handler as query_handler
from search import handler as search_handler


def _extract_path(request) -> str:
    if isinstance(request, dict):
        raw_path = request.get("path")
        if isinstance(raw_path, str):
            return raw_path

        raw_url = request.get("url")
        if isinstance(raw_url, str):
            return raw_url

    if hasattr(request, "path"):
        value = getattr(request, "path")
        if isinstance(value, str):
            return value

    if hasattr(request, "url"):
        value = getattr(request, "url")
        if isinstance(value, str):
            return value

    return "/"


def handler(request):
    path = _extract_path(request)
    if path in {"/", "/health", "/api/health"}:
        return health_handler(request)
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

    # Preserve a simple legacy fallback for local checks.
    return query_handler(request)
