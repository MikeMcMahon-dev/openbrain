from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api._openbrain_api import query_payload, search_payload, ingest_payload, cors_headers


app = FastAPI()


def _request_metadata(request: Request, method: str | None = None) -> dict[str, Any]:
    return {
        "method": (method or request.method or "POST").upper(),
        "headers": dict(request.headers),
        "query": dict(request.query_params),
    }


def _response(status: int, body: Any) -> JSONResponse:
    headers = cors_headers()
    return JSONResponse(status_code=status, content=body, headers=headers)


class TutorQueryRequest(BaseModel):
    query: str
    mode: str = "explain"
    n_results: int = 5
    student_attempt: str | None = None
    owner: str | None = None


class SearchRequest(BaseModel):
    query: str
    n_results: int = 5
    owner: str | None = None


class IngestRequest(BaseModel):
    source_type: str
    source: str
    subject: str | None = None
    topic: str | None = None
    owner: str | None = None


@app.get("/")
def health() -> dict[str, str]:
    return {"status": "openbrain online"}


@app.get("/health")
def health_endpoint() -> dict[str, str]:
    return {"status": "openbrain online"}


@app.post("/search")
def search_endpoint(request: Request, payload: SearchRequest) -> JSONResponse:
    body = payload.model_dump()
    status, response_body = search_payload(body, _request_metadata(request))
    return _response(status, response_body)


@app.get("/search")
def search_get_endpoint(
    request: Request,
    query: str,
    n_results: int = 5,
) -> JSONResponse:
    status, response_body = search_payload(
        {"query": query, "n_results": n_results},
        _request_metadata(request),
    )
    return _response(status, response_body)


@app.post("/query")
def query_endpoint(request: Request, payload: TutorQueryRequest) -> JSONResponse:
    body = payload.model_dump()
    status, response_body = query_payload(body, _request_metadata(request))
    return _response(status, response_body)


@app.post("/generate_quiz")
def generate_quiz(request: Request, payload: TutorQueryRequest) -> JSONResponse:
    body = payload.model_dump()
    body["mode"] = "quiz"
    status, response_body = query_payload(body, _request_metadata(request))
    return _response(status, response_body)


@app.post("/generate_flashcards")
def generate_flashcards(request: Request, payload: TutorQueryRequest) -> JSONResponse:
    body = payload.model_dump()
    body["mode"] = "flashcards"
    status, response_body = query_payload(body, _request_metadata(request))
    return _response(status, response_body)


@app.post("/ingest")
def ingest_endpoint(request: Request, payload: IngestRequest) -> JSONResponse:
    status, response_body = ingest_payload(payload.model_dump(), _request_metadata(request))
    return _response(status, response_body)

