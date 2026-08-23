"""HTTP handler for MCP protocol endpoint."""

from __future__ import annotations

import json
from typing import Any

from api._openbrain_api import (
    _require_tool_auth,
    fetch_payload,
    ingest_payload,
    parse_request,
    query_payload,
    response_payload,
    search_payload,
)
from api.canonical_systems import CANONICAL_SYSTEMS
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
                        "description": "Optional: your attempt at answering the question.",
                    },
                },
            },
        },
        {
            "name": "search",
            "description": (
                "Skim your vault: returns lightweight PREVIEWS (heading, ~40-word snippet, "
                "and relevance signals) for the top matches WITHOUT their full text. Use this "
                "FIRST to cheaply see what's available, then call `fetch` with the id(s) you "
                "actually need in full. Prefer search+fetch over `query` when you only need "
                "one or two specific notes — it keeps far less text in context."
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
                        "description": "Maximum number of previews to return. Default: 5.",
                    },
                },
            },
        },
        {
            "name": "fetch",
            "description": (
                "Fetch the FULL text of specific vault notes by id. Pass the id(s) returned "
                "by `search`: a result's `id` fetches just that section, its `document_id` "
                "fetches the whole note (all sections). Owner-scoped — you can only fetch "
                "your own notes."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["ids"],
                "properties": {
                    "ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Note ids from a prior `search` result (max 20).",
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
                        "description": "Content type: 'text' for inline notes, 'url' for webpages.",
                        "enum": ["text", "url"],
                    },
                    "source": {
                        "type": "string",
                        "description": "For text: the verbatim content. For url: the URL to fetch.",
                    },
                    "subject": {
                        "type": "string",
                        "description": "Subject or domain label (e.g., Biology, Kubernetes).",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Topic tag (e.g., Photosynthesis, RBAC).",
                    },
                    "domain": {
                        "type": "string",
                        "description": (
                            "Canonical knowledge domain. Choose the closest existing "
                            "value; do not invent new domains."
                        ),
                        "enum": ["Network", "K8s", "Security", "Study", "OpenBrain", "Personal"],
                    },
                    "environment": {
                        "type": "string",
                        "description": (
                            "Canonical lifecycle environment. Choose the closest existing "
                            "value; do not invent new environments."
                        ),
                        "enum": ["Production", "Lab", "Study", "Archive"],
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional topical tags from the controlled vocabulary, e.g. "
                            "IaC, Terraform, Ansible, Bash, Python, K8s, CKA, Network, "
                            "Security, Architecture, AI, Ops, Lab, Production, OpenBrain, "
                            "Homelab, SpectreNet, Personal, Family, Annie, Science, Biology, "
                            "Geometry, Math, Study, Health, Career, Interview. Use existing "
                            "tags where possible; novel tags are queued for human approval, "
                            "not auto-applied."
                        ),
                    },
                    "system": {
                        "type": "string",
                        "description": (
                            "Namespace this note belongs to — the ADR-018 supersession "
                            "pivot. REQUIRED whenever component is set. Choose the closest "
                            "existing value; do not invent new systems."
                        ),
                        "enum": sorted(CANONICAL_SYSTEMS),
                    },
                    "component": {
                        "type": "string",
                        "description": (
                            "Living-doc identity (ADR-008). Set ONLY for a canonical "
                            "current-state document that should REPLACE its prior version "
                            "on re-ingest (e.g. dns-current-state) — never for append-only "
                            "session notes. Requires system. Re-ingesting the same "
                            "(system, component) retires the old version via a "
                            "supersession event."
                        ),
                    },
                    "valid_from": {
                        "type": "string",
                        "description": (
                            "ISO-8601 fact-onset (valid-time), e.g. 2026-07-15. Set only "
                            "when the fact became true BEFORE now (backdating a change you "
                            "are recording after the fact); otherwise omit — it defaults to "
                            "ingest time. ADR-018 bitemporality."
                        ),
                    },
                    "plan_token": {
                        "type": "string",
                        "description": (
                            "Token returned by plan_ingest, valid 10 minutes and bound to this "
                            "exact content. Required when plan enforcement is on. Call "
                            "plan_ingest first — it shows which living docs already exist so "
                            "you can tell an update from a new note."
                        ),
                    },
                    "acknowledged_not_updating": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Every component from the plan's `candidates` that this note does "
                            "NOT update. Required (in full) when you are writing an append-only "
                            "note and the plan listed candidates. Naming them is the record "
                            "that you saw the list and chose."
                        ),
                    },
                    "decline_reason": {
                        "type": "string",
                        "description": (
                            "Why this is a new record rather than an update. Required only when "
                            "declining a candidate the plan scored as a close match."
                        ),
                    },
                },
            },
        },
        {
            "name": "plan_ingest",
            "description": (
                "PREVIEW an ingest before committing it — the vault's equivalent of a "
                "'terraform plan'. Writes nothing. Returns the living docs already in scope, "
                "what a commit would supersede, and a plan_token to pass to ingest.\n\n"
                "Call this FIRST for any note about a system you have written about before. "
                "You cannot reliably tell an update from a new note without seeing what "
                "already exists, and this is the only way to see it."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["source"],
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "The exact content you intend to ingest, verbatim.",
                    },
                    "system": {
                        "type": "string",
                        "description": (
                            "Namespace you believe this belongs to. Supplying it gives an exact "
                            "list of that system's living docs; omitting it falls back to "
                            "similarity, which is weaker."
                        ),
                        "enum": sorted(CANONICAL_SYSTEMS),
                    },
                    "component": {
                        "type": "string",
                        "description": (
                            "Living-doc identity you believe this updates. Supplying it makes "
                            "the plan report exactly which row would be superseded."
                        ),
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
                        "description": "Knowledge chunks to draw questions from. Default: 5.",
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


def _wrap_content(body: Any) -> dict[str, Any]:
    """Wrap tool result in MCP content envelope (spec 2024-11-05)."""
    text = json.dumps(body, indent=2) if isinstance(body, dict) else str(body)
    return {"content": [{"type": "text", "text": text}]}


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
        return _wrap_content(body if isinstance(body, dict) else json.loads(body))

    elif name == "search":
        normalized = {
            "query": arguments.get("query"),
            "n_results": arguments.get("n_results", 5),
        }
        normalized = {k: v for k, v in normalized.items() if v is not None}
        status, body = search_payload(normalized, metadata)
        return _wrap_content(body if isinstance(body, dict) else json.loads(body))

    elif name == "fetch":
        status, body = fetch_payload({"ids": arguments.get("ids")}, metadata)
        return _wrap_content(body if isinstance(body, dict) else json.loads(body))

    elif name == "ingest":
        normalized = {
            "source_type": arguments.get("source_type"),
            "source": arguments.get("source"),
            "subject": arguments.get("subject", ""),
            "topic": arguments.get("topic", ""),
            # The inputSchema advertises domain/environment/tags; forward them so the
            # per-owner honor/derive policy (ingest_payload) can honor the override.
            # Omitting them silently misfiled technical content into the Study default.
            "domain": arguments.get("domain"),
            "environment": arguments.get("environment"),
            "tags": arguments.get("tags"),
            # ADR-008/018 living-doc identity. This allowlist DROPS anything not named
            # here, so advertising these in the inputSchema without forwarding them
            # would silently write a keyless row that looks like a successful ingest —
            # the exact failure that stranded the FlightSim doc (2026-08-21).
            "system": arguments.get("system"),
            "component": arguments.get("component"),
            "valid_from": arguments.get("valid_from"),
            # Plan/apply handshake. Same allowlist trap as above — advertising these without
            # forwarding them would 409 every commit with no way for the client to comply.
            "plan_token": arguments.get("plan_token"),
            "acknowledged_not_updating": arguments.get("acknowledged_not_updating"),
            "decline_reason": arguments.get("decline_reason"),
        }
        normalized = {k: v for k, v in normalized.items() if v}
        status, body = ingest_payload(normalized, metadata)
        return _wrap_content(body if isinstance(body, dict) else json.loads(body))

    elif name == "plan_ingest":
        from api._openbrain_api import request_context
        from api.ingest_plan import build_plan
        owner, _tenant = request_context(metadata)
        return _wrap_content(build_plan(
            arguments.get("source") or "", owner,
            system=arguments.get("system"), component=arguments.get("component")))

    elif name == "generate_quiz":
        normalized = {
            "query": arguments.get("query"),
            "n_results": arguments.get("n_results", 5),
            "mode": "quiz",
        }
        status, body = query_payload(normalized, metadata)
        return _wrap_content(body if isinstance(body, dict) else json.loads(body))

    elif name == "generate_flashcards":
        normalized = {
            "query": arguments.get("query"),
            "n_results": arguments.get("n_results", 5),
            "mode": "flashcards",
        }
        status, body = query_payload(normalized, metadata)
        return _wrap_content(body if isinstance(body, dict) else json.loads(body))

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
