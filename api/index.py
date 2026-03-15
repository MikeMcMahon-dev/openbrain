from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler

from api import app


def _normalize_path(raw_path: str | None) -> str:
    if not raw_path:
        return "/"
    return raw_path.split("?", 1)[0] or "/"


def _build_event(request: "handler") -> dict[str, object]:
    path = _normalize_path(request.path)

    query = {}
    query_string = ""
    if "?" in request.path:
        query_string = request.path.split("?", 1)[1]
        for segment in query_string.split("&"):
            if not segment:
                continue
            if "=" in segment:
                key, value = segment.split("=", 1)
            else:
                key, value = segment, ""
            query[key] = value

    body: bytes | str | None = None
    content_length = request.headers.get("Content-Length")
    if content_length:
        try:
            length = int(content_length)
            if length > 0:
                body_bytes = request.rfile.read(length)
                body = body_bytes.decode("utf-8")
        except Exception:
            body = None

    return {
        "path": path,
        "method": request.command,
        "headers": dict(request.headers),
        "queryStringParameters": query,
        "body": body,
    }


def _write_response(request: "handler", response: dict[str, object]) -> None:
    status = int(response.get("statusCode", 200))
    payload = response.get("body", "")
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(payload).encode("utf-8")

    request.send_response(status)
    headers = response.get("headers")
    if isinstance(headers, dict):
        for name, value in headers.items():
            request.send_header(str(name), str(value))

    request.send_header("Content-Type", "application/json")
    request.send_header("Content-Length", str(len(data)))
    request.end_headers()
    request.wfile.write(data)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        event = _build_event(self)
        response = app.handler(event)
        _write_response(self, response)

    def do_POST(self):
        event = _build_event(self)
        response = app.handler(event)
        _write_response(self, response)

    def do_OPTIONS(self):
        event = _build_event(self)
        response = app.handler(event)
        _write_response(self, response)
