"""HTTP handler for MCP protocol endpoint."""
from __future__ import annotations

import json
from typing import Any

from api._openbrain_api import _require_tool_auth, ingest_payload, parse_request, query_payload, response_payload
from api.chatgpt import _inject_token_owner


def handler(request: dict) -> dict[str, Any]:
    """Handle MCP HTTP requests."""
    payload, metadata = parse_request(request)

    # Validate authentication first
    is_authorized, reason, resolved_owner = _require_tool_auth(metadata)
    if not is_authorized:
        return response_payload(
            401,
            {
                "error": "unauthorized",
                "message": reason,
                "status": 401,
            },
        )

    # Inject owner into metadata for context resolution
    if resolved_owner:
        _inject_token_owner(metadata, resolved_owner)

    # Handle OPTIONS (CORS preflight)
    if metadata.get("method") == "OPTIONS":
        return response_payload(200, {"ok": True})

    # Handle GET for tool discovery
    if metadata.get("method") == "GET":
        return _handle_discovery()

    # Parse payload for POST requests
    if not isinstance(payload, dict):
        payload = {}

    # Route based on JSON-RPC method
    method = payload.get("jsonrpc")
    if method == "2.0":
        return _handle_jsonrpc(payload, metadata)

    # Default: unsupported request
    return response_payload(
        400,
        {
            "error": "invalid_request",
            "message": "Expected JSON-RPC 2.0 format or GET for discovery",
            "status": 400,
        },
    )


def _handle_jsonrpc(payload: dict, metadata: dict) -> dict[str, Any]:
    """Handle JSON-RPC 2.0 request."""
    method = payload.get("method")
    params = payload.get("params", {})
    request_id = payload.get("id")

    try:
        if method == "initialize":
            # MCP initialize message
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "logging": {},
                    "tools": {},
                },
                "serverInfo": {
                    "name": "openbrain-claude",
                    "version": "1.0.0",
                },
            }
            return response_payload(200, _jsonrpc_response(result, request_id))

        elif method and method.startswith("notifications/"):
            # MCP notifications are one-way — no response body, just acknowledge
            return response_payload(200, {})

        elif method == "tools/list":
            # List available tools
            tools = _list_tools()
            return response_payload(
                200,
                _jsonrpc_response({"tools": tools}, request_id),
            )

        elif method == "tools/call":
            # Execute a tool
            tool_name = params.get("name")
            arguments = params.get("arguments", {})

            try:
                result = _call_tool(tool_name, arguments, metadata)
                return response_payload(200, _jsonrpc_response(result, request_id))
            except Exception as e:
                error = {
                    "code": -32603,
                    "message": f"Tool execution failed: {str(e)}",
                }
                return response_payload(200, _jsonrpc_error(error, request_id))

        else:
            error = {
                "code": -32601,
                "message": f"Unknown method: {method}",
            }
            return response_payload(200, _jsonrpc_error(error, request_id))

    except Exception as e:
        error = {
            "code": -32603,
            "message": f"Server error: {str(e)}",
        }
        return response_payload(200, _jsonrpc_error(error, request_id))


def _list_tools() -> list[dict]:
    """Return list of available tools with schemas."""
    return [
        {
            "name": "query",
            "description": (
                "Search your personal knowledge vault using hybrid keyword + vector retrieval. "
                "Returns relevant chunks and tutor guidance for explaining concepts."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The question or topic to look up in your vault.",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Maximum number of knowledge chunks to return. Default: 5.",
                    },
                    "mode": {
                        "type": "string",
                        "description": "Response mode: explain (default), quiz, or flashcards.",
                        "enum": ["explain", "quiz", "flashcards"],
                    },
                    "student_attempt": {
                        "type": "string",
                        "description": "Optional: your attempt at answering. Tutor will respond to it.",
                    },
                },
            },
        },
        {
            "name": "ingest",
            "description": (
                "Save new content to your knowledge vault. Use source_type='text' to save "
                "notes directly from conversation. Use 'url' to fetch and save webpages."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["source_type", "source"],
                "properties": {
                    "source_type": {
                        "type": "string",
                        "description": "Type of content: 'text' for inline notes, 'url' for webpages.",
                        "enum": ["text", "url"],
                    },
                    "source": {
                        "type": "string",
                        "description": "For text: the verbatim content. For url: the full URL to fetch.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Subject or domain label (e.g., Biology, Kubernetes).",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Topic tag (e.g., Photosynthesis, RBAC).",
                    },
                },
            },
        },
        {
            "name": "generate_quiz",
            "description": (
                "Generate quiz questions from your vault notes on a given topic. "
                "Returns quiz-formatted content ready to present."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic to generate quiz questions about.",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of knowledge chunks to draw questions from. Default: 5.",
                    },
                },
            },
        },
        {
            "name": "generate_flashcards",
            "description": (
                "Generate flashcard decks from your vault notes on a given topic. "
                "Returns front/back pairs for spaced repetition."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Topic to generate flashcards for.",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of knowledge chunks to draw cards from. Default: 5.",
                    },
                },
            },
        },
    ]


def _call_tool(name: str, arguments: dict, metadata: dict) -> dict[str, Any]:
    """Execute a tool and return result."""
    if name == "query":
        normalized = {
            "query": arguments.get("query"),
            "n_results": arguments.get("n_results", 5),
            "mode": arguments.get("mode", "explain"),
            "student_attempt": arguments.get("student_attempt"),
        }
        normalized = {k: v for k, v in normalized.items() if v is not None}
        status, body = query_payload(normalized, metadata)
        return body if isinstance(body, dict) else json.loads(body)

    elif name == "ingest":
        normalized = {
            "source_type": arguments.get("source_type"),
            "source": arguments.get("source"),
            "subject": arguments.get("subject", ""),
            "topic": arguments.get("topic", ""),
        }
        normalized = {k: v for k, v in normalized.items() if v}
        status, body = ingest_payload(normalized, metadata)
        return body if isinstance(body, dict) else json.loads(body)

    elif name == "generate_quiz":
        normalized = {
            "query": arguments.get("query"),
            "n_results": arguments.get("n_results", 5),
            "mode": "quiz",
        }
        status, body = query_payload(normalized, metadata)
        return body if isinstance(body, dict) else json.loads(body)

    elif name == "generate_flashcards":
        normalized = {
            "query": arguments.get("query"),
            "n_results": arguments.get("n_results", 5),
            "mode": "flashcards",
        }
        status, body = query_payload(normalized, metadata)
        return body if isinstance(body, dict) else json.loads(body)

    else:
        raise ValueError(f"Unknown tool: {name}")


def _handle_discovery() -> dict[str, Any]:
    """Return tool definitions for discovery (GET request)."""
    return response_payload(
        200,
        {
            "tools": _list_tools(),
            "message": "Use POST /mcp/messages with JSON-RPC 2.0 to call tools",
        },
    )


def _jsonrpc_response(result: Any, request_id: Any) -> dict[str, Any]:
    """Wrap result in JSON-RPC 2.0 response format."""
    return {
        "jsonrpc": "2.0",
        "result": result,
        "id": request_id,
    }


def _jsonrpc_error(error: dict[str, Any], request_id: Any) -> dict[str, Any]:
    """Wrap error in JSON-RPC 2.0 error format."""
    return {
        "jsonrpc": "2.0",
        "error": error,
        "id": request_id,
    }
