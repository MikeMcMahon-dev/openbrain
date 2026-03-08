from fastapi import FastAPI
from pathlib import Path
from rank_bm25 import BM25Okapi
import chromadb, threading, hashlib
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from sentence_transformers import SentenceTransformer

app = FastAPI()

# locate project root
project_root = Path(__file__).resolve().parent.parent

# resolve key directories
brain_index_path = project_root / "brain_index"
vault_path = project_root / "vault"

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

documents = all_docs["documents"]
metadatas = all_docs["metadatas"]

import re

tokenized_corpus = [
    re.findall(r"\b\w+\b", doc.lower())
    for doc in documents
]

bm25 = BM25Okapi(tokenized_corpus)

print(f"Keyword index ready ({len(documents)} chunks)")

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
                    "chunk": neighbor_chunk
                }
            )

            if result["documents"]:
                expanded.append(result["documents"][0])

        except Exception:
            pass

    return "\n".join(expanded)



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
        embedding = model.encode(chunk["text"])
        
        hash_input = f"{filepath}:{chunk_index}"
        doc_id = hashlib.md5(hash_input.encode()).hexdigest()

        metadata = {
            "id": doc_id,
            "file": filename,
            "section": section,
            "heading": chunk["heading"],
            "chunk": chunk_index
        }

        collection.upsert(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[chunk["text"]],
            metadatas=[metadata]
        )

print("Embedding model ready.")
handler = VaultChangeHandler()
observer = Observer()
observer.schedule(handler, str(vault_path), recursive=True)
observer.start()

print("Vault watcher started")




@app.get("/")
def health():
    return {"status": "openbrain online"}


@app.post("/search")
def search_brain(query: str, n_results: int = 5):

    embedding = model.encode(query)
    tokenized_query = re.findall(r"\b\w+\b", query.lower())

    results = collection.query(
        query_embeddings=[embedding],
        n_results=n_results
    )
    seen = set()
    structured_results = []

    docs = results["documents"][0]
    metas = results["metadatas"][0]
    distances = results["distances"][0]

    keyword_scores = bm25.get_scores(tokenized_query)

    top_keyword_indices = sorted(
        range(len(keyword_scores)),
        key=lambda i: keyword_scores[i],
        reverse=True
    )[:n_results]

    for doc, meta, dist in zip(docs, metas, distances):
        doc_id = f"{meta['file']}:{meta['chunk']}"

        if doc_id in seen:
            continue

        seen.add(doc_id)
        
        score = 1 - dist
        
        doc_text = doc.lower()
        query_words = query.lower().split()

        keyword_hits = sum(1 for w in query_words if w in doc_text)
        score += keyword_hits * 0.5
        
        context = expand_context(meta, doc)

        structured_results.append({
            "score": 1 - dist,
            "file": meta.get("file"),
            "section": meta.get("section"),
            "heading": meta.get("heading"),
            "text": context
        })
    
    for idx in top_keyword_indices:

        doc = documents[idx]
        meta = metadatas[idx]

        doc_id = (meta.get("file"), meta.get("chunk"))

        if doc_id in seen:
            continue

        seen.add(doc_id)

        context = expand_context(meta, doc)

        structured_results.append({
            "score": keyword_scores[idx],
            "file": meta.get("file"),
            "section": meta.get("section"),
            "heading": meta.get("heading"),
            "text": context
        })

        if len(structured_results) >= n_results * 2:
            break
        
        structured_results.sort(key=lambda x: x["score"], reverse=True)

    return {
        "query": query,
        "results": structured_results
    }