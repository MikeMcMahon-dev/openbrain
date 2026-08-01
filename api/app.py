from __future__ import annotations

from api.chatgpt import handler as chatgpt_handler
from api.claude import handler as claude_handler
from api.cron_session_report import handler as cron_session_report_handler
from api.cron_supersession_reconcile import handler as cron_supersession_reconcile_handler
from api.fetch import handler as fetch_handler
from api.generate_flashcards import handler as flashcards_handler
from api.generate_quiz import handler as quiz_handler
from api.health import handler as health_handler
from api.ingest import handler as ingest_handler
from api.mcp_http import handler as mcp_handler
from api.oauth import handle_authorize, handle_discovery, handle_token
from api.ob2_state import (
    handle_confirm_supersession,
    handle_ingest_state,
    handle_propose_supersession,
    handle_query_state,
)
from api.ob2_wiki import handle_compile_wiki, handle_get_wiki
from api.query import handler as query_handler
from api.search import handler as search_handler
from api.session_report import handler as session_report_handler


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
    if path in {"/fetch", "/api/fetch"}:
        return fetch_handler(request)
    if path in {"/query", "/api/query"}:
        return query_handler(request)
    if path in {"/generate_quiz", "/api/generate_quiz"}:
        return quiz_handler(request)
    if path in {"/generate_flashcards", "/api/generate_flashcards"}:
        return flashcards_handler(request)
    if path in {"/ingest", "/api/ingest"}:
        return ingest_handler(request)
    if path in {"/openbrain_query", "/tools/openbrain_query"}:
        return chatgpt_handler(request, tool_mode="query")
    if path in {"/openbrain_generate_quiz", "/tools/openbrain_generate_quiz"}:
        return chatgpt_handler(request, tool_mode="generate_quiz")
    if path in {"/openbrain_generate_flashcards", "/tools/openbrain_generate_flashcards"}:
        return chatgpt_handler(request, tool_mode="generate_flashcards")
    if path in {"/openbrain_ingest", "/tools/openbrain_ingest"}:
        return chatgpt_handler(request, tool_mode="ingest")
    if path in {"/claude_query", "/tools/claude_query"}:
        return claude_handler(request, tool_mode="query")
    if path in {"/claude_generate_quiz", "/tools/claude_generate_quiz"}:
        return claude_handler(request, tool_mode="generate_quiz")
    if path in {"/claude_generate_flashcards", "/tools/claude_generate_flashcards"}:
        return claude_handler(request, tool_mode="generate_flashcards")
    if path in {"/claude_ingest", "/tools/claude_ingest"}:
        return claude_handler(request, tool_mode="ingest")
    if path in {"/mcp/messages", "/api/mcp/messages"}:
        return mcp_handler(request)
    if path in {"/.well-known/oauth-authorization-server"}:
        return handle_discovery(request)
    if path in {"/authorize"}:
        return handle_authorize(request)
    if path in {"/token"}:
        return handle_token(request)
    if path in {"/session_report", "/api/session_report"}:
        return session_report_handler(request)
    if path in {"/api/cron/session_report"}:
        return cron_session_report_handler(request)

    if path in {"/api/cron/supersession_reconcile"}:
        return cron_supersession_reconcile_handler(request)
    if path in {"/api/ingest_state"}:
        return handle_ingest_state(request)
    if path in {"/api/propose_supersession"}:
        return handle_propose_supersession(request)
    if path in {"/api/confirm_supersession"}:
        return handle_confirm_supersession(request)
    if path in {"/api/query_state"}:
        return handle_query_state(request)
    if path in {"/api/compile_wiki"}:
        return handle_compile_wiki(request)
    if path.startswith("/api/wiki/") and len(path) > len("/api/wiki/"):
        return handle_get_wiki(request, page_name=path[len("/api/wiki/"):])

    return {
        "statusCode": 404,
        "headers": {"content-type": "application/json"},
        "body": "{\"error\":\"not_found\",\"status\":404}",
    }
