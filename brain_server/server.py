import hashlib
import re
import sys
import threading
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

# validate required directories
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


class IngestRequest(BaseModel):
    source_type: str
    source: str
    subject: str | None = None
    topic: str | None = None


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


def _normalize_mode(mode: str) -> str:
    value = (mode or "explain").lower().strip()
    return value if value in {"explain", "quiz", "flashcards"} else "explain"


def _keyword_fallback(query: str, n_results: int):
    query_tokens = re.findall(r"\b\w+\b", query.lower())
    if not query_tokens:
        return []

    scores = [
        (idx, sum(1 for term in query_tokens if term in (doc or "").lower()))
        for idx, doc in enumerate(documents)
    ]
    scores.sort(key=lambda item: item[1], reverse=True)

    results = []
    for idx, score in scores[:n_results]:
        if score <= 0:
            break
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
                "text": context,
            }
        )
    return results


def search_with_tutor(request: TutorQueryRequest):
    results = search_brain(request.query, request.n_results)
    if not results:
        results = _keyword_fallback(request.query, request.n_results)

    context_chunks = [
        {
            "source": result.get("file"),
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
            "status": "tutor_module_unavailable",
            "message": "Tutor prompt module not importable from server process.",
        }
    else:
        tutor_payload = build_tutor_packet(
            _normalize_mode(request.mode),
            request.query,
            context_chunks,
            request.student_attempt,
        )

    return {
        "query": request.query,
        "mode": tutor_payload.get("mode"),
        "results": results,
        "tutor": tutor_payload,
    }


@app.get("/")
def health():
    return {"status": "openbrain online"}


@app.post("/search")
def search_brain_endpoint(query: str, n_results: int = 5):
    return search_brain(query, n_results)


def search_brain(query: str, n_results: int = 5):
    embedding = as_python_list(model.encode(query, convert_to_numpy=True))
    tokenized_query = re.findall(r"\b\w+\b", query.lower())

    results = collection.query(query_embeddings=[embedding], n_results=n_results)
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
                "section": meta.get("section"),
                "heading": meta.get("heading"),
                "content_type": meta.get("content_type"),
                "text": context,
            }
        )

    for idx in top_keyword_indices:
        doc = documents[idx]
        meta = metadatas[idx]
        identifier = doc_id_from_meta(meta)

        if identifier in seen:
            continue

        seen.add(identifier)

        context = expand_context(meta, doc)

        structured_results.append(
            {
                "score": keyword_scores[idx],
                "file": meta.get("file"),
                "section": meta.get("section"),
                "heading": meta.get("heading"),
                "content_type": meta.get("content_type"),
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
    return {
        "status": "planned",
        "message": (
            "Ingest endpoint scaffolded only. Use scripts/ingest.py now, then bind this endpoint "
            "to an async worker queue in the MCP layer."
        ),
        "source_type": request.source_type,
        "source": request.source,
        "subject": request.subject,
        "topic": request.topic,
    }
