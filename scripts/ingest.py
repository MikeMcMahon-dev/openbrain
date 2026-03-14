import hashlib
import tomllib
from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer
from transformers import logging

from chunking import chunk_markdown, chunk_text_by_tokens
from ingestors import load_docx_documents, load_markdown_documents, load_pdf_documents, load_url_documents

logging.set_verbosity_error()


MODEL_NAME = "BAAI/bge-small-en"
MIN_CHUNK_LENGTH = 80
COLLECTION_NAME = "openbrain"


script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent


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


def _chunk_documents_for_source(document: dict[str, Any]) -> list[dict[str, str]]:
    if document.get("content_type") == "markdown":
        return chunk_markdown(document.get("text", ""))
    return chunk_text_by_tokens(document.get("text", ""))


def _build_metadata(document: dict[str, Any], heading: str, chunk_index: int) -> dict[str, Any]:
    metadata = {
        "source": document["source"],
        "file": document["file"],
        "section": document.get("section", "root"),
        "heading": heading,
        "chunk": chunk_index,
        "content_type": document["content_type"],
    }

    if document.get("subject"):
        metadata["subject"] = document["subject"]
    if document["topic"]:
        metadata["topic"] = document["topic"]

    return metadata


print("Loading configuration...")
config_path = project_root / "config" / "imports.toml"

with open(config_path, "rb") as f:
    config = tomllib.load(f)

data_sources = config.get("data_sources", {})

documents = []

obsidian_path = project_root / _normalize_file_name(data_sources.get("obsidian", "vault"))
if obsidian_path.exists():
    documents.extend(load_markdown_documents(obsidian_path, subject="engineering", topic="notes"))
    print(f"Loaded {len(documents)} documents after markdown input")
else:
    print(f"Skipping Obsidian ingestion path: {obsidian_path} (not found)")

for pdf_source in _resolve_data_sources(config, "pdf"):
    pdf_path = Path(pdf_source)
    if not pdf_path.is_absolute():
        pdf_path = project_root / pdf_path
    if pdf_path.exists():
        documents.extend(load_pdf_documents(pdf_path, subject="study_materials", topic="pdf"))
    else:
        print(f"Skipping PDF path: {pdf_path} (not found)")

for docx_source in _resolve_data_sources(config, "docx"):
    docx_path = Path(docx_source)
    if not docx_path.is_absolute():
        docx_path = project_root / docx_path
    if docx_path.exists():
        documents.extend(load_docx_documents(docx_path, subject="study_materials", topic="docx"))
    else:
        print(f"Skipping DOCX path: {docx_path} (not found)")

for source in _resolve_data_sources(config, "urls"):
    if isinstance(source, str) and source.strip():
        docs = load_url_documents(source, subject="study_materials", topic="url")
        documents.extend(docs)
    else:
        print(f"Skipping invalid URL entry: {source}")

print(f"Prepared {len(documents)} total documents")
for document in documents[:3]:
    print(document["source"], document.get("content_type"), "->", len(document.get("text", "")))

print("\nLoading embedding model...")
model = SentenceTransformer(MODEL_NAME)
client = chromadb.PersistentClient(path=str(project_root / "brain_index"))
collection = client.get_or_create_collection(COLLECTION_NAME)

print("Model loaded successfully.")

print("\nIndexing documents...")
chunk_count = 0

for doc in documents:
    path = Path(doc["source"])
    file_name = path.name if doc["content_type"] != "url" else doc["file"]

    chunks = _chunk_documents_for_source(doc)
    print(doc["source"], "→", len(chunks), "chunks")

    for i, chunk in enumerate(chunks):
        chunk_text = chunk["text"].strip()
        if len(chunk_text) < MIN_CHUNK_LENGTH:
            continue

        chunk_id = f"{doc['id']}_{i}"
        heading = chunk.get("heading", "root")

        embedding = _to_list(model.encode(chunk_text))
        metadata = _build_metadata(doc, heading, i)

        collection.upsert(
            ids=[chunk_id],
            embeddings=[embedding],
            documents=[chunk_text],
            metadatas=[metadata],
        )
        chunk_count += 1

print(f"Indexed {len(documents)} documents into {chunk_count} chunks.")
