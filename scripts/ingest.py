from __future__ import annotations

import hashlib
import os
import tomllib
from pathlib import Path
from typing import Any

from chunking import chunk_markdown, chunk_text_by_tokens
from ingestors import (
    load_docx_documents,
    load_markdown_documents,
    load_pdf_documents,
    load_url_documents,
)
from transformers import logging

logging.set_verbosity_error()


MODEL_NAME = "BAAI/bge-small-en"
MIN_CHUNK_LENGTH = 80
DEFAULT_OWNER = "default_user"
DEFAULT_USER_ID = "default_user"
SUPABASE_ENABLED_BY_DEFAULT = False
TENANT_ID = os.getenv("OPENBRAIN_DEFAULT_TENANT_ID", "family").strip() or "family"

_MODEL: Any | None = None


script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent


try:
    from api._openbrain_api import embedding_request as _request_embedding
except Exception:  # pragma: no cover
    _request_embedding = None


def _load_model() -> Any | None:
    global _MODEL
    if _MODEL is not None:
        return _MODEL

    try:
        from sentence_transformers import SentenceTransformer

        _MODEL = SentenceTransformer(MODEL_NAME)
        return _MODEL
    except Exception:
        return None


def _to_list(value: Any) -> list[float]:
    if isinstance(value, list):
        return value
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def _resolve_data_sources(config: dict[str, Any], source_name: str) -> list[Any]:
    value = config.get("data_sources", {}).get(source_name, [])
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    return list(value)


def _normalize_file_name(value: str) -> str:
    return value.strip() or "unknown"


def _load_toml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("rb") as file:
        return tomllib.load(file)


def _build_connection_string(database_config: dict[str, Any]) -> str | None:
    env_connection = os.getenv("OPENBRAIN_SUPABASE_CONNECTION_STRING")
    if env_connection and env_connection.strip():
        return env_connection.strip()

    explicit_connection = database_config.get("connection_string")
    if isinstance(explicit_connection, str) and explicit_connection.strip():
        return explicit_connection.strip()

    host = str(database_config.get("host", "")).strip()
    user = str(database_config.get("user", "")).strip()
    password = str(database_config.get("password", "")).strip()
    name = str(database_config.get("name", "postgres")).strip()
    port = str(database_config.get("port", 5432)).strip()
    if not host or not user or not password:
        return None

    from urllib.parse import quote_plus

    quoted_user = quote_plus(user)
    quoted_password = quote_plus(password)
    return f"postgresql://{quoted_user}:{quoted_password}@{host}:{port}/{name}"


def _connect_supabase(database_config: dict[str, Any]) -> tuple[Any | None, str | None]:
    if not database_config.get("enabled", False):
        return None, "supabase integration is disabled in config/supabase.toml"

    connection_string = _build_connection_string(database_config)
    if not connection_string:
        return None, "missing connection string"

    try:
        import psycopg
    except ImportError:
        return None, (
            "psycopg is required for Supabase connections; "
            "add psycopg to requirements and install dependencies."
        )

    connect_timeout = int(database_config.get("connect_timeout_seconds", 10))
    autocommit = bool(database_config.get("connection_autocommit", True))

    try:
        connection = psycopg.connect(
            connection_string,
            connect_timeout=connect_timeout,
            autocommit=autocommit,
        )
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return connection, None
    except Exception as exc:
        return None, f"database connection failed: {exc}"


def _normalize_user(value: Any) -> str:
    candidate = str(value).strip() if value else ""
    return candidate or DEFAULT_OWNER


def _chunk_documents_for_source(document: dict[str, Any]) -> list[dict[str, str]]:
    if document.get("content_type") == "markdown":
        return chunk_markdown(document.get("text", ""))
    return chunk_text_by_tokens(document.get("text", ""))


def _build_metadata(document: dict[str, Any], heading: str, chunk_index: int) -> dict[str, Any]:
    owner = document.get("owner") or DEFAULT_OWNER
    user_id = document.get("user_id") or owner or DEFAULT_USER_ID

    metadata = {
        "source": str(document.get("source") or ""),
        "file": document.get("file"),
        "section": document.get("section", "root"),
        "heading": heading,
        "chunk": chunk_index,
        "content_type": document["content_type"],
        "owner": owner,
        "user_id": user_id,
    }

    if document.get("subject"):
        metadata["subject"] = document["subject"]
    if document["topic"]:
        metadata["topic"] = document["topic"]
    return metadata


def _enrich_document(document: dict[str, Any], owner: str, user_id: str, source: str) -> None:
    document["owner"] = owner
    document["user_id"] = user_id
    document["file"] = document.get("file") or source.split("/")[-1] or source


def _introspect_columns(connection: Any) -> set[str]:
    rows = connection.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'thoughts';
        """
    ).fetchall()

    columns: set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            columns.add(str(row["column_name"]))
        elif isinstance(row, tuple) and row:
            columns.add(str(row[0]))
    return columns


def _compute_embedding(text: str) -> list[float] | None:
    if _request_embedding is not None:
        try:
            values = _request_embedding(text)
            if isinstance(values, list):
                return [float(x) for x in values]
        except Exception:
            print("Embedding request failed, falling back to local model.")

    model = _load_model()
    if model is None:
        return None

    try:
        return _to_list(model.encode(text))
    except Exception as exc:
        print(f"Local fallback embedder failed: {exc}")
        return None


def _build_chunk_identifier(
    owner: str,
    document_id: str,
    chunk_index: int,
    chunk_text: str,
    tenant_id: str,
) -> tuple[str, str]:
    digest = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
    source_chunk_id = f"{document_id}:{chunk_index}:{tenant_id}"
    row_id = hashlib.md5(f"{tenant_id}:{owner}:{source_chunk_id}:{digest[:12]}".encode("utf-8")).hexdigest()
    return row_id, source_chunk_id


def _insert_rows(
    connection: Any,
    rows: list[dict[str, Any]],
    available_columns: set[str],
) -> tuple[int, int]:
    if not rows:
        return 0, 0

    inserted = 0
    skipped = 0
    columns = list(rows[0].keys())
    values_sql = ", ".join(["%s"] * len(columns))
    insert_sql = (
        f"INSERT INTO public.thoughts ({', '.join(columns)}) "
        f"VALUES ({values_sql}) "
        "ON CONFLICT DO NOTHING"
    )

    for row in rows:
        if not set(row.keys()).issubset(available_columns):
            skipped += 1
            continue

        try:
            cursor = connection.cursor()
            cursor.execute(insert_sql, [row[column] for column in columns])
            if cursor.rowcount and cursor.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
            cursor.close()
        except Exception as exc:
            print(f"Insert skipped for {row.get('source_chunk_id')}: {exc}")
            skipped += 1

    return inserted, skipped


print("Loading configuration...")
config_path = project_root / "config" / "imports.toml"
supabase_config_path = project_root / "config" / "supabase.toml"

config = _load_toml_file(config_path)
supabase_config = _load_toml_file(supabase_config_path).get("database", {})
supabase_enabled = bool(supabase_config.get("enabled", SUPABASE_ENABLED_BY_DEFAULT))
supabase_config["enabled"] = supabase_enabled

data_sources = config.get("data_sources", {})
pipeline_owner = _normalize_user(data_sources.get("owner"))
pipeline_user_id = _normalize_user(data_sources.get("user_id") or pipeline_owner)
tenant_id = TENANT_ID

documents: list[dict[str, Any]] = []

obsidian_path = project_root / _normalize_file_name(data_sources.get("obsidian", "vault"))
if obsidian_path.exists():
    for doc in load_markdown_documents(obsidian_path, subject="engineering", topic="notes"):
        doc["source_type"] = "obsidian"
        _enrich_document(doc, pipeline_owner, pipeline_user_id, doc["source"])
        documents.append(doc)
    print(f"Loaded {len(documents)} documents after markdown input")
else:
    print(f"Skipping Obsidian ingestion path: {obsidian_path} (not found)")

for pdf_source in _resolve_data_sources(config, "pdf"):
    pdf_path = Path(pdf_source)
    if not pdf_path.is_absolute():
        pdf_path = project_root / pdf_path
    if pdf_path.exists():
        for doc in load_pdf_documents(pdf_path, subject="study_materials", topic="pdf"):
            doc["source_type"] = "pdf"
            _enrich_document(doc, pipeline_owner, pipeline_user_id, doc["source"])
            documents.append(doc)
    else:
        print(f"Skipping PDF path: {pdf_path} (not found)")

for docx_source in _resolve_data_sources(config, "docx"):
    docx_path = Path(docx_source)
    if not docx_path.is_absolute():
        docx_path = project_root / docx_path
    if docx_path.exists():
        for doc in load_docx_documents(docx_path, subject="study_materials", topic="docx"):
            doc["source_type"] = "docx"
            _enrich_document(doc, pipeline_owner, pipeline_user_id, doc["source"])
            documents.append(doc)
    else:
        print(f"Skipping DOCX path: {docx_path} (not found)")

for source in _resolve_data_sources(config, "urls"):
    if isinstance(source, str) and source.strip():
        for doc in load_url_documents(source, subject="study_materials", topic="url"):
            doc["source_type"] = "url"
            _enrich_document(doc, pipeline_owner, pipeline_user_id, doc["source"])
            documents.append(doc)
    else:
        print(f"Skipping invalid URL entry: {source}")

print(f"Prepared {len(documents)} total documents")
for document in documents[:3]:
    print(document["source"], document.get("content_type"), "->", len(document.get("text", "")))

if not documents:
    print("No documents prepared for ingestion.")
    raise SystemExit(0)

print("\nConnecting to Supabase...")
supabase_conn, supabase_error = _connect_supabase(supabase_config)
if not supabase_conn:
    print(f"Supabase connection not established: {supabase_error}")
    raise SystemExit(2)

try:
    available_columns = _introspect_columns(supabase_conn)
    if "content" not in available_columns or "metadata" not in available_columns:
        print("ERROR: thoughts table does not have expected columns content/metadata.")
        raise SystemExit(2)

    print(f"Writing {len(documents)} docs into Supabase tenant '{tenant_id}'")

    rows: list[dict[str, Any]] = []
    for document in documents:
        source_type = str(document.get("source_type") or "obsidian").strip() or "obsidian"
        chunks = _chunk_documents_for_source(document)
        print(document["source"], "→", len(chunks), "chunks")

        for index, chunk in enumerate(chunks):
            chunk_text = chunk.get("text", "").strip()
            if len(chunk_text) < MIN_CHUNK_LENGTH:
                continue

            heading = chunk.get("heading", "root")
            row_id, source_chunk_id = _build_chunk_identifier(
                pipeline_owner,
                str(document["id"]),
                index,
                chunk_text,
                tenant_id,
            )
            embedding = _compute_embedding(chunk_text)
            metadata = _build_metadata(document, heading, index)
            metadata["source_type"] = source_type

            row: dict[str, Any] = {
                "id": row_id,
                "content": chunk_text,
                "tenant_id": tenant_id,
                "source_type": source_type,
                "source_uri": document.get("source"),
                "source_chunk_id": source_chunk_id,
                "document_id": document["id"],
                "chunk_id": index,
                "content_hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                "metadata": metadata,
                "created_by_user_id": pipeline_user_id,
                "created_by_user_login": pipeline_owner,
                "slack_username": pipeline_owner,
                "visibility": "private",
                "subject": document.get("subject"),
                "topic": document.get("topic"),
            }

            if embedding is not None and "embedding" in available_columns:
                row["embedding"] = embedding
            if "source_team_id" in available_columns:
                row["source_team_id"] = document.get("source_team_id")
            if "source_workspace_id" in available_columns:
                row["source_workspace_id"] = document.get("source_workspace_id")
            if "source_channel_id" in available_columns:
                row["source_channel_id"] = document.get("source_channel_id")

            rows.append({key: value for key, value in row.items() if key in available_columns})

    inserted, skipped = _insert_rows(supabase_conn, rows, available_columns)
    print(f"Prepared chunks: {len(rows)}; inserted: {inserted}; skipped: {skipped}.")
finally:
    try:
        supabase_conn.close()
        print("Supabase database connection closed.")
    except Exception as exc:
        print(f"Failed to close Supabase connection cleanly: {exc}")

