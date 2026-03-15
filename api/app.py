from __future__ import annotations

from urllib.parse import urlparse

from api._openbrain_api import response_payload
from api.generate_flashcards import handler as flashcards_handler
from api.generate_quiz import handler as quiz_handler
from api.health import handler as health_handler
from api.ingest import handler as ingest_handler
from api.query import handler as query_handler
from api.search import handler as search_handler


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
