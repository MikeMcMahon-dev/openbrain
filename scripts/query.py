import chromadb
import re
from sentence_transformers import SentenceTransformer
from pathlib import Path
from typing import Any

from tutor import build_tutor_packet


MODE_OPTIONS = {"explain", "quiz", "flashcards"}


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b\w+\b", text.lower())


def build_fallback_hits(
    query: str,
    n_results: int,
    documents: list[str],
    metadatas: list[dict[str, Any]],
) -> list[tuple[str, dict[str, Any], float]]:
    terms = [term for term in tokenize(query) if len(term) > 2]
    if not terms:
        return []

    scores = []
    for index, (doc_text, meta) in enumerate(zip(documents, metadatas)):
        text = doc_text.lower()
        hits = sum(1 for term in terms if term in text)
        if hits == 0:
            continue
        scores.append((doc_text, meta, float(hits)))

    scores.sort(key=lambda value: value[2], reverse=True)
    return scores[:n_results]


def search_brain(query: str, n_results: int = 3):
    print(f"\nSearching for: {query}")
    query_embedding = model.encode(query, convert_to_numpy=True)
    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results
    )

    docs = results.get("documents") or [[]]
    metas = results.get("metadatas") or [[]]
    distances = results.get("distances") or [[]]

    retrieval = []
    for doc, meta, distance in zip(docs[0], metas[0], distances[0]):
        retrieval.append({
            "source": meta.get("source"),
            "file": meta.get("file"),
            "section": meta.get("section"),
            "heading": meta.get("heading"),
            "content_type": meta.get("content_type"),
            "chunk": meta.get("chunk"),
            "text": doc,
            "score": float(distance),
        })

    if retrieval:
        return retrieval

    print("Vector search returned no chunks. Running keyword fallback.")
    all_docs = collection.get()
    documents = all_docs.get("documents") or []
    all_metas = all_docs.get("metadatas") or []
    for doc, meta, score in build_fallback_hits(query, n_results, documents, all_metas):
        retrieval.append({
            "source": meta.get("source"),
            "file": meta.get("file"),
            "section": meta.get("section"),
            "heading": meta.get("heading"),
            "content_type": meta.get("content_type"),
            "chunk": meta.get("chunk"),
            "text": doc,
            "score": score,
        })

    return retrieval


def render_mode_output(mode: str, query_text: str, results: list[dict[str, Any]], attempt: str | None = None):
    packet = build_tutor_packet(mode, query_text, results, attempt)
    print("\nQuery:", packet["question"])
    print("Mode:", packet["mode"])
    print("\nTutor prompt:")
    print("\nTutor guidance packet:\n")
    print(packet["tutor_prompt"])
    print(packet["instructions"])
    if packet["rules"]:
        print("Tutor rules:")
        for rule in packet["rules"]:
            print("-", rule)
    print("\nContext previews:")
    print("Context used:", len(packet["context_used"]))
    for result in results:
        print("\nSOURCE:", result["source"])
        print(result["text"][:280])

    return packet


project_root = Path(__file__).resolve().parent.parent

print("Loading embedding model...")
model = SentenceTransformer("BAAI/bge-small-en")
print("Model loaded.")

client = chromadb.PersistentClient(path=str(project_root / "brain_index"))

collection = client.get_collection("openbrain")

query = input("\nAsk a question: ").strip()
while not query:
    query = input("Ask a question: ").strip()

mode = input("Tutor mode (explain, quiz, flashcards): ").strip().lower()
if mode not in MODE_OPTIONS:
    mode = "explain"

results = search_brain(query, n_results=3)
packet = render_mode_output(mode, query, results)

student_attempt = input("\nTry an answer first (optional): ").strip()
if student_attempt:
    print("\nGreat, here is the next Socratic step:\n")
    packet = build_tutor_packet(mode, query, results, student_attempt)
    print(packet["instructions"])

print("\nTop results included:", len(results))
