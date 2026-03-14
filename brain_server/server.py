import hashlib
import re
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

import chromadb
from fastapi import FastAPI
from pydantic import BaseModel
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


app = FastAPI()

# locate project root
project_root = Path(__file__).resolve().parent.parent

# resolve key directories
brain_index_path = project_root / "brain_index"
vault_path = project_root / "vault"

sys.path.append(str(project_root / "scripts"))

try:
    from tutor import build_tutor_packet
except Exception:  # pragma: no cover
    build_tutor_packet = None


print("\nStarting OpenBrain server...")
print(f"Project root: {project_root}")

if not brain_index_path.exists():
    raise RuntimeError("brain_index directory not found. Run ingestion first.")

if not vault_path.exists():
    raise RuntimeError("vault directory not found.")

print("Vault directory: OK")
print("Brain index: OK")

# load vector database
client = chromadb.PersistentClient(path=str(project_root / "brain_index"))
collection = client.get_collection("openbrain")

print("Building keyword index...")
all_docs = collection.get()
documents = all_docs.get("documents") or []
metadatas = all_docs.get("metadatas") or []

tokenized_corpus = [
    re.findall(r"\b\w+\b", doc.lower())
    for doc in documents
]

bm25 = BM25Okapi(tokenized_corpus)
print(f"Keyword index ready ({len(documents)} chunks)")


class TutorQueryRequest(BaseModel):
    query: str
    mode: str = "explain"
    n_results: int = 5
    student_attempt: str | None = None
    owner: str = "default_user"


class IngestRequest(BaseModel):
    source_type: str
    source: str
    subject: str | None = None
    topic: str | None = None
    owner: str = "default_user"


def as_python_list(value: Any) -> list[float]:
    if isinstance(value, list):
        return value
    if hasattr(value, "tolist"):
        return value.tolist()
    return list(value)


def expand_context(meta, doc, window=1):
    base_chunk = meta["chunk"]
    source_file = meta["file"]

    expanded = [doc]

    for offset in range(-window, window + 1):
        if offset == 0:
            continue

        neighbor_chunk = base_chunk + offset

        try:
            result = collection.get(
                where={
                    "file": source_file,
                    "chunk": neighbor_chunk,
                }
            )
            neighbor_documents = result.get("documents") or []

            if neighbor_documents:
                expanded.append(neighbor_documents[0])
        except Exception as exc:
            print(f"Error fetching neighbor chunk {neighbor_chunk}: {exc}")

    return "\n".join(expanded)


def doc_id_from_meta(meta):
    return (meta.get("file"), meta.get("chunk"))


# load embedding model once
print("Loading embedding model...")
model = SentenceTransformer("BAAI/bge-small-en")


def _normalize_mode(mode: str) -> str:
    value = (mode or "explain").lower().strip()
    return value if value in {"explain", "quiz", "flashcards"} else "explain"


class VaultChangeHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_changed_file = None
        self.reindex_timer = None

    def on_modified(self, event):
        if event.is_directory:
            return

        if not event.src_path.endswith(".md"):
            return

        self.last_changed_file = event.src_path

        if self.reindex_timer:
            self.reindex_timer.cancel()

        self.reindex_timer = threading.Timer(5, self.trigger_reindex)
        self.reindex_timer.start()

    def trigger_reindex(self):
        if self.last_changed_file is None:
            return

        print(f"Reindexing changed file: {self.last_changed_file}")
        reindex_file(self.last_changed_file)
        self.reindex_timer = None


def split_into_chunks(text):
    return [{"heading": "root", "text": text}]


def reindex_file(filepath):
    filepath = Path(filepath)
    print(f"Indexing updated file: {filepath}")

    text = filepath.read_text(encoding="utf-8")
    section = str(filepath.parent)
    filename = filepath.name
    chunks = split_into_chunks(text)

    for chunk_index, chunk in enumerate(chunks):
        if len(chunk["text"].strip()) < 80:
            continue

        print("Processing chunk:", chunk["heading"])
        embedding = as_python_list(model.encode(chunk["text"], convert_to_numpy=True))

        hash_input = f"{filepath}:{chunk_index}"
        doc_id = hashlib.md5(hash_input.encode()).hexdigest()

        metadata = {
            "id": doc_id,
            "file": filename,
            "section": section,
            "heading": chunk["heading"],
            "chunk": chunk_index,
            "content_type": "markdown",
            "owner": "default_user",
            "user_id": "default_user",
        }

        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[chunk["text"]],
            metadatas=[metadata],
        )


print("Embedding model ready.")
handler = VaultChangeHandler()
observer = Observer()
observer.schedule(handler, str(vault_path), recursive=True)
observer.start()

print("Vault watcher started")


def _keyword_fallback(query: str, n_results: int, owner: str):
    query_tokens = re.findall(r"\b\w+\b", query.lower())
    if not query_tokens:
        return []

    scored = []
    for idx, doc in enumerate(documents):
        meta = metadatas[idx]
        owner_filter = (owner or "").strip() or ""
        if owner_filter and meta.get("owner") and meta.get("owner") != owner_filter:
            continue

        score = sum(1 for term in query_tokens if term in (doc or "").lower())
        if score <= 0:
            continue

        scored.append((score, idx))

    scored.sort(key=lambda value: value[0], reverse=True)

    results = []
    for score, idx in scored[:n_results]:
        meta = metadatas[idx]
        text = documents[idx]
        context = expand_context(meta, text)
        results.append(
            {
                "score": float(score),
                "file": meta.get("file"),
                "section": meta.get("section"),
                "heading": meta.get("heading"),
                "content_type": meta.get("content_type"),
                "source": meta.get("source"),
                "text": context,
            }
        )

    return results


def search_with_tutor(request: TutorQueryRequest):
    owner = (request.owner or "default_user").strip() or "default_user"
    results = search_brain(request.query, request.n_results, owner)
    if not results:
        results = _keyword_fallback(request.query, request.n_results, owner)

    context_chunks = [
        {
            "source": result.get("source") or result.get("file"),
            "file": result.get("file"),
            "section": result.get("section"),
            "heading": result.get("heading"),
            "text": result.get("text", ""),
        }
        for result in results
    ]

    if build_tutor_packet is None:
        tutor_payload = {
            "mode": _normalize_mode(request.mode),
            "rules": [
                "Ask the student to try first.",
                "Use short, simple language for a middle school learner.",
                "Explain ideas step by step.",
                "Encourage effort and curiosity before confirming answers.",
            ],
            "tutor_prompt": "Tutor module unavailable in this runtime.",
            "context_used": context_chunks,
        }
    else:
        tutor_payload = build_tutor_packet(
            _normalize_mode(request.mode),
            request.query,
            context_chunks,
            request.student_attempt,
        )

    return {
        "mode": tutor_payload.get("mode", _normalize_mode(request.mode)),
        "question": request.query,
        "rules": tutor_payload.get("rules", []),
        "tutor_prompt": tutor_payload.get("tutor_prompt", ""),
        "context_used": tutor_payload.get("context_used", []),
        "results": results,
    }


@app.get("/")
def health():
    return {"status": "openbrain online"}


@app.post("/search")
def search_brain_endpoint(query: str, n_results: int = 5):
    return search_brain(query, n_results, owner="default_user")


def search_brain(query: str, n_results: int = 5, owner: str = "default_user"):
    normalized_owner = (owner or "").strip() or "default_user"
    embedding = as_python_list(model.encode(query, convert_to_numpy=True))
    tokenized_query = re.findall(r"\b\w+\b", query.lower())

    where_clause = {"owner": normalized_owner}

    try:
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where_clause,
        )
        documents_by_query = results.get("documents") or []
        metadatas_by_query = results.get("metadatas") or []
        distances_by_query = results.get("distances") or []
    except Exception as exc:
        print(f"Owner-filtered vector query failed: {exc}. Falling back without owner filter.")
        results = collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
        )
        documents_by_query = results.get("documents") or []
        metadatas_by_query = results.get("metadatas") or []
        distances_by_query = results.get("distances") or []

    if not documents_by_query or not metadatas_by_query or not distances_by_query:
        return []

    seen = set()
    structured_results = []

    docs = documents_by_query[0] or []
    metas = metadatas_by_query[0] or []
    distances = distances_by_query[0] or []

    keyword_scores = bm25.get_scores(tokenized_query)
    top_keyword_indices = sorted(
        range(len(keyword_scores)),
        key=lambda i: keyword_scores[i],
        reverse=True,
    )[:n_results]

    for doc, meta, dist in zip(docs, metas, distances):
        if meta.get("owner") and meta.get("owner") != normalized_owner:
            continue

        identifier = doc_id_from_meta(meta)
        if identifier in seen:
            continue

        seen.add(identifier)

        doc_text = doc.lower()
        query_words = query.lower().split()
        keyword_hits = sum(1 for word in query_words if word in doc_text)
        score = (1 - dist) + (keyword_hits * 0.5)
        context = expand_context(meta, doc)

        structured_results.append(
            {
                "score": score,
                "file": meta.get("file"),
                "source": meta.get("source"),
                "section": meta.get("section"),
                "heading": meta.get("heading"),
                "content_type": meta.get("content_type"),
                "owner": meta.get("owner"),
                "text": context,
            }
        )

    for idx in top_keyword_indices:
        doc = documents[idx]
        meta = metadatas[idx]

        if meta.get("owner") and meta.get("owner") != normalized_owner:
            continue

        identifier = doc_id_from_meta(meta)
        if identifier in seen:
            continue

        seen.add(identifier)

        context = expand_context(meta, doc)

        structured_results.append(
            {
                "score": keyword_scores[idx],
                "file": meta.get("file"),
                "source": meta.get("source"),
                "section": meta.get("section"),
                "heading": meta.get("heading"),
                "content_type": meta.get("content_type"),
                "owner": meta.get("owner"),
                "text": context,
            }
        )

        if len(structured_results) >= n_results * 2:
            break

    return structured_results


@app.post("/query")
def query_endpoint(request: TutorQueryRequest):
    return search_with_tutor(request)


@app.post("/generate_quiz")
def generate_quiz(request: TutorQueryRequest):
    request.mode = "quiz"
    return search_with_tutor(request)


@app.post("/generate_flashcards")
def generate_flashcards(request: TutorQueryRequest):
    request.mode = "flashcards"
    return search_with_tutor(request)


@app.post("/ingest")
def ingest_endpoint(request: IngestRequest):
    source_type = (request.source_type or "").strip().lower()
    normalized_source = (request.source or "").strip()

    allowed_source_types = {"obsidian", "pdf", "docx", "url"}
    status = "queued"
    if normalized_source and source_type in allowed_source_types:
        status = "accepted"

    return {
        "ingest_id": uuid.uuid4().hex,
        "status": status,
        "source_type": source_type,
        "source": normalized_source,
        "owner": (request.owner or "default_user").strip() or "default_user",
        "subject": request.subject,
        "topic": request.topic,
        "message": (
            "Ingest endpoint scaffolded. Payload is accepted by API contract;"
            " async execution wiring is a next step in MCP layer."
        ),
    }
