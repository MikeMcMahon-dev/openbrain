import tomllib, chromadb, hashlib

from pathlib import Path
from sentence_transformers import SentenceTransformer
from transformers import logging
logging.set_verbosity_error()


script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent

config_path = project_root / "config" / "imports.toml"

with open(config_path, "rb") as f:
    config = tomllib.load(f)

vault_relative = config["data_sources"]["obsidian"]
vault_path = project_root / vault_relative

print(vault_path.resolve())

markdown_files = list(vault_path.rglob("*.md"))
                                       
print(f"Found {len(markdown_files)} markdown files")
for file in markdown_files[:5]:
    print(file)

documents = []

for file in markdown_files:
    text = file.read_text()
    doc_id = hashlib.md5(text.encode("utf-8")).hexdigest()
    documents.append({
        "id": doc_id,
        "text": text,
        "source": str(file)
    })

print(f"Loaded {len(documents)} documents")
print("\nLoading embedding model...")

model = SentenceTransformer("BAAI/bge-small-en")
client = chromadb.PersistentClient(path=str(project_root / "brain_index"))
collection = client.get_or_create_collection("openbrain")

print("Model loaded successfully.")

# Markdown chunking function
def chunk_markdown(text):

    chunks = []
    current_chunk = []
    current_heading = "root"

    for line in text.splitlines():

        if line.startswith("#"):

            if current_chunk:
                chunks.append({
                    "heading": current_heading,
                    "text": "\n".join(current_chunk)
                })
                current_chunk = []

            current_heading = line.strip()

        current_chunk.append(line)

    if current_chunk:
        chunks.append({
            "heading": current_heading,
            "text": "\n".join(current_chunk)
        })

    return chunks

print("\nIndexing documents...")
chunk_count = 0

for doc in documents:
    path = Path(doc["source"])

    file_name = path.name
    vault_section = str(path.parent).replace("vault/", "")

    chunks = chunk_markdown(doc["text"])
    print(doc["source"], "→", len(chunks), "chunks")
    for i, chunk in enumerate(chunks):
        if len(chunk["text"].strip()) < 80:
            continue

        chunk_id = f"{doc['id']}_{i}"

        embedding = model.encode(chunk["text"])

        collection.upsert(
            ids=[chunk_id],
            embeddings=[embedding],
            documents=[chunk["text"]],
            metadatas=[{
                "source": doc["source"],
                "file": file_name,
                "section": vault_section,
                "heading": chunk["heading"],
                "chunk": i
            }]
        )
        chunk_count += 1

print(f"Indexed {len(documents)} documents into {chunk_count} chunks.")