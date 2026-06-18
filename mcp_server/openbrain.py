#!/usr/bin/env python3
"""OpenBrain MCP server — exposes vault query and ingest as Claude tools."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx
import mcp.server.stdio
import mcp.types as types
from mcp.server import Server

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _read_env() -> None:
    """Load .env.local without requiring dotenv."""
    for path in (_PROJECT_ROOT / ".env", _PROJECT_ROOT / ".env.local"):
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and value and not os.getenv(key):
                os.environ[key] = value


_read_env()

BASE_URL = os.getenv("OPENBRAIN_BASE_URL", "https://openbrain-rouge.vercel.app").rstrip("/")
TOKEN = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")

# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------


def _call(path: str, body: dict) -> str:
    headers = {"Content-Type": "application/json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    try:
        resp = httpx.post(f"{BASE_URL}{path}", json=body, headers=headers, timeout=30)
        return resp.text
    except Exception as exc:
        return json.dumps({"error": str(exc)})


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

app = Server("openbrain")


@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="openbrain_query",
            description=(
                "Search Mike's personal knowledge vault using hybrid keyword + vector retrieval. "
                "Always call this first before answering questions about his notes, projects, "
                "infrastructure, or study material."
            ),
            inputSchema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Question or topic to look up."},
                    "n_results": {"type": "integer", "description": "Max chunks to return. Default 5."},
                },
            },
        ),
        types.Tool(
            name="openbrain_ingest",
            description=(
                "Save a new note or piece of information into the knowledge vault. "
                "Use source_type 'text' to save inline content directly from the conversation."
            ),
            inputSchema={
                "type": "object",
                "required": ["source_type", "source"],
                "properties": {
                    "source_type": {"type": "string", "description": "Type of content. Use 'text' for inline notes."},
                    "source": {"type": "string", "description": "The content to save (for source_type 'text')."},
                    "subject": {"type": "string", "description": "Subject or domain label."},
                    "topic": {"type": "string", "description": "Topic tag."},
                    "domain": {
                        "type": "string",
                        "description": "Canonical knowledge domain. Choose the closest existing value; do not invent new domains.",
                        "enum": ["Network", "K8s", "Security", "Study", "OpenBrain", "Personal"],
                    },
                    "environment": {
                        "type": "string",
                        "description": "Canonical lifecycle environment. Choose the closest existing value; do not invent new environments.",
                        "enum": ["Production", "Lab", "Study", "Archive"],
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional topical tags from the controlled vocabulary, e.g. IaC, Terraform, "
                            "Ansible, Bash, Python, K8s, CKA, Network, Security, Architecture, AI, Ops, "
                            "Lab, Production, OpenBrain, Homelab, SpectreNet, Personal, Family, Annie, "
                            "Science, Biology, Geometry, Math, Study, Health, Career, Interview. Use "
                            "existing tags where possible; novel tags are queued for human approval, not "
                            "auto-applied."
                        ),
                    },
                },
            },
        ),
        types.Tool(
            name="openbrain_generate_quiz",
            description="Generate quiz questions from vault notes on a given topic.",
            inputSchema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Topic to generate quiz questions about."},
                    "n_results": {"type": "integer", "description": "Number of chunks to draw from. Default 5."},
                },
            },
        ),
        types.Tool(
            name="openbrain_generate_flashcards",
            description="Generate flashcards from vault notes on a given topic.",
            inputSchema={
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "description": "Topic to generate flashcards for."},
                    "n_results": {"type": "integer", "description": "Number of chunks to draw from. Default 5."},
                },
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "openbrain_query":
        result = _call("/openbrain_query", arguments)
    elif name == "openbrain_ingest":
        result = _call("/openbrain_ingest", {"tool_input": arguments})
    elif name == "openbrain_generate_quiz":
        result = _call("/openbrain_generate_quiz", arguments)
    elif name == "openbrain_generate_flashcards":
        result = _call("/openbrain_generate_flashcards", arguments)
    else:
        result = json.dumps({"error": f"Unknown tool: {name}"})

    return [types.TextContent(type="text", text=result)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    async def main():
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())

    asyncio.run(main())
