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

# Run as a script (`python mcp_server/openbrain.py`), sys.path[0] is mcp_server/, so the
# repo root has to be added before `api.*` is importable. Only the canonical-system
# vocabulary is pulled in — the server stays a thin HTTP client otherwise.
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from api.canonical_systems import CANONICAL_SYSTEMS  # noqa: E402


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
                    "n_results": {
                        "type": "integer",
                        "description": "Max chunks to return. Default 5.",
                    },
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
                    "source_type": {
                        "type": "string",
                        "description": "Type of content. Use 'text' for inline notes.",
                    },
                    "source": {
                        "type": "string",
                        "description": "The content to save (for source_type 'text').",
                    },
                    "subject": {"type": "string", "description": "Subject or domain label."},
                    "topic": {"type": "string", "description": "Topic tag."},
                    "domain": {
                        "type": "string",
                        "description": (
                            "Canonical knowledge domain. Choose the closest existing value; "
                            "do not invent new domains."
                        ),
                        "enum": ["Network", "K8s", "Security", "Study", "OpenBrain", "Personal"],
                    },
                    "environment": {
                        "type": "string",
                        "description": (
                            "Canonical lifecycle environment. Choose the closest existing value; "
                            "do not invent new environments."
                        ),
                        "enum": ["Production", "Lab", "Study", "Archive"],
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional topical tags from the controlled vocabulary, e.g. IaC, "
                            "Terraform, Ansible, Bash, Python, K8s, CKA, Network, Security, "
                            "Architecture, AI, Ops, Lab, Production, OpenBrain, Homelab, "
                            "SpectreNet, Personal, Family, Annie, Science, Biology, Geometry, "
                            "Math, Study, Health, Career, Interview. Use existing tags where "
                            "possible; novel tags are queued for human approval, not auto-applied."
                        ),
                    },
                    "system": {
                        "type": "string",
                        "description": (
                            "Namespace this note belongs to — the ADR-018 supersession pivot. "
                            "REQUIRED whenever component is set. Choose the closest existing "
                            "value; do not invent new systems."
                        ),
                        "enum": sorted(CANONICAL_SYSTEMS),
                    },
                    "component": {
                        "type": "string",
                        "description": (
                            "Living-doc identity (ADR-008). Set ONLY for a canonical "
                            "current-state document that should REPLACE its prior version on "
                            "re-ingest (e.g. dns-current-state) — never for append-only session "
                            "notes. Requires system. Re-ingesting the same (system, component) "
                            "retires the old version via a supersession event."
                        ),
                    },
                    "valid_from": {
                        "type": "string",
                        "description": (
                            "ISO-8601 fact-onset (valid-time), e.g. 2026-07-15. Set only when "
                            "the fact became true BEFORE now (backdating a change you are "
                            "recording after the fact); otherwise omit — it defaults to ingest "
                            "time. ADR-018 bitemporality."
                        ),
                    },
                    "plan_token": {
                        "type": "string",
                        "description": (
                            "Token from openbrain_plan_ingest, valid 10 minutes and bound to "
                            "this exact content. Required when plan enforcement is on. Run the "
                            "plan first — it lists the living docs that already exist, which is "
                            "the only way to tell an update from a new note."
                        ),
                    },
                    "acknowledged_not_updating": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Every component from the plan's `candidates` that this note does "
                            "NOT update. Required in full when writing an append-only note and "
                            "the plan listed candidates. Naming them records that you saw the "
                            "list and chose."
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
        ),
        types.Tool(
            name="openbrain_plan_ingest",
            description=(
                "PREVIEW an ingest before committing it — a 'terraform plan' for the vault. "
                "Writes nothing. Returns the living docs already in scope, what a commit would "
                "supersede, and a plan_token to pass to openbrain_ingest.\n\n"
                "Run this FIRST for any note about a system you have written about before. You "
                "cannot reliably tell an update from a new note without seeing what already "
                "exists, and this is the only way to see it."
            ),
            inputSchema={
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
        ),
        types.Tool(
            name="openbrain_propose_retirement",
            description=(
                "REQUEST that a row be removed from the vault. Queues it for human approval and "
                "removes NOTHING — Mike reviews every request and only he can execute one.\n\n"
                "Use this when you find content that is wrong, superseded, or a dead artifact, "
                "rather than leaving it to compete with current knowledge. You may only propose "
                "removal of rows you own.\n\n"
                "Prefer method='retire' (the default): content is preserved and marked "
                "historical, still reachable by an as-of read. 'delete' is irreversible and only "
                "legal when nothing references the row. A retire FORECLOSES a later delete."
            ),
            inputSchema={
                "type": "object",
                "required": ["target_id", "rationale"],
                "properties": {
                    "target_id": {
                        "type": "string",
                        "description": "id of the knowledge row to remove.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": (
                            "WHY this row should go, in prose, at least 20 characters. This is "
                            "what the human actually reads."
                        ),
                    },
                    "method": {
                        "type": "string",
                        "enum": ["retire", "delete"],
                        "description": (
                            "retire (default, reversible) or delete (irreversible, only when "
                            "nothing references the row)."
                        ),
                    },
                    "reason_code": {
                        "type": "string",
                        "enum": ["explicit", "component_collision", "contradiction_confirmed",
                                 "ttl_expiry", "manual", "migration"],
                        "description": "Category for the removal. Defaults to 'manual'.",
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
                    "query": {
                        "type": "string",
                        "description": "Topic to generate quiz questions about.",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Number of chunks to draw from. Default 5.",
                    },
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
                    "n_results": {
                        "type": "integer",
                        "description": "Number of chunks to draw from. Default 5.",
                    },
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
    elif name == "openbrain_plan_ingest":
        result = _call("/openbrain_plan_ingest", {"tool_input": arguments})
    elif name == "openbrain_propose_retirement":
        result = _call("/openbrain_propose_retirement", {"tool_input": arguments})
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
