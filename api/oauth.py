"""Retired OAuth endpoints.

The former flow issued provisioned owner tokens without authenticating a user.
Keep explicit denial handlers so imports or alternate routing cannot revive it.
Provisioned bearer-token MCP clients do not use these endpoints.
"""
from __future__ import annotations

from typing import Any


def _disabled() -> dict[str, Any]:
    return {
        "statusCode": 410,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": '{"error":"oauth_disabled"}',
    }


def handle_discovery(request: Any) -> dict[str, Any]:
    return _disabled()


def handle_authorize(request: Any) -> dict[str, Any]:
    return _disabled()


def handle_token(request: Any) -> dict[str, Any]:
    return _disabled()
