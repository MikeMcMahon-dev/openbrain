# OpenBrain Architecture

OpenBrain is a local knowledge retrieval system for semantic access to
engineering notes in an Obsidian vault.

It functions as a **personal RAG engine**.

---

## Core Components

## 1. Obsidian Vault

Source knowledge base.

Contents:

- infrastructure notes
- automation documentation
- study materials
- homelab design decisions

---

## 2. Ingestion Pipeline

`scripts/ingest.py`

Responsible for:

- reading markdown files
- chunking by headings
- filtering small chunks
- generating embeddings
- storing vectors in ChromaDB

---

## 3. Vector Database

`brain_index/`

Powered by **ChromaDB**.

Stores:

- embeddings
- chunk text
- metadata

Metadata fields:

- `file`
- `section`
- `heading`
- `chunk`
- `source`

---

## 4. Embedding Model

`BAAI/bge-small-en`

Chosen because:

- high retrieval quality
- small footprint
- fast CPU inference

---

## 5. FastAPI Retrieval Server

`brain_server/server.py`

Provides:

- `POST /search` (development alias)
- MCP-like routes in scaffold:
  - `POST /ingest`
  - `POST /query`
  - `POST /generate_quiz`
  - `POST /generate_flashcards`

Hybrid search:

- vector similarity
- BM25 keyword retrieval
- keyword-first surfacing with vector fallback

---

## 6. Live Vault Indexing

Uses:

- watchdog

Pipeline:

`vault change -> debounce timer -> reindex_file() -> vector upsert`

---

## Retrieval Strategy

Search ranking combines semantic and keyword signals:

- vector similarity
- keyword-first boost

This improves accuracy for technical queries.

Example:

query: `terraform modules`

- semantic: related automation notes
- keyword-first: exact terraform matches

---

## System Design Goals

OpenBrain prioritizes:

- local-first operation
- fast indexing
- AI tool compatibility
- extensible architecture

---

## Long-Term Direction

OpenBrain will support:

- AI agents querying the knowledge base
- automated documentation retrieval
- engineering memory persistence
- code assistant integration
