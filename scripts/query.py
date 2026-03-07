import chromadb
from sentence_transformers import SentenceTransformer
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent

print("Loading embedding model...")
model = SentenceTransformer("BAAI/bge-small-en")
print("Model loaded.")

client = chromadb.PersistentClient(path=str(project_root / "brain_index"))

collection = client.get_collection("openbrain")

query = input("\nAsk a question: ")

query_embedding = model.encode(query)

results = collection.query(
    query_embeddings=[query_embedding],
    n_results=3
)

print("\nTop results:\n")

for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
    print("SOURCE:", meta["source"])
    print(doc[:300])
    print("\n---\n")