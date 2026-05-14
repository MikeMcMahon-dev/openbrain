# ADR-009: Wiki Layer — Explicit Compilation, Not Auto-Compile

**Status:** Accepted
**Date:** 2026-05-13

## Context

Every session re-derives understanding from raw knowledge chunks. Nothing compounds. A query
for "current SpectreNet state" retrieves N individual records and synthesizes them at query
time, every time. There is no persistent compiled artifact representing "the current understood
state of SpectreNet."

This is the gap Karpathy identifies: a wiki is a persistent, compounding artifact where
understanding accumulates rather than being re-derived from scratch each session.

Two compilation strategies were considered:

1. **Auto-compile on ingest** — compile (or recompile) the relevant wiki page every time
   a new knowledge record is ingested
2. **Explicit compile on demand** — ingest marks affected wiki pages stale; a separate API
   call triggers compilation when ready

## Decision

**Explicit compilation only.** Ingest never triggers compilation automatically.

Auto-compile on ingest is rejected for the following reasons:

- **Latency** — every ingest call becomes slow: DB write + LLM call in the request path
- **Cost** — N ingests during a session = N LLM compilation calls, most of which are
  immediately superseded by the next ingest
- **Thrashing** — a wiki compiled after ingest #3 in a burst of 10 is stale by ingest #4
- **Partial state** — the compiled wiki reflects an incomplete picture mid-burst; it will
  be wrong until the burst finishes
- **Error coupling** — a compilation failure (LLM timeout, rate limit) would block ingest

Instead:
- Ingest sets `is_stale = true` on any `wiki_pages` row with `domain` or `system` matching
  the ingested record
- Human or scheduled job calls `POST /api/compile_wiki` when a coherent snapshot is desired
- `compile_wiki` reads all `current` knowledge records for the target domain/system,
  synthesizes a wiki page via LLM, writes to `wiki_pages`, sets `is_stale = false`

The `wiki_pages` table stores compiled markdown pages only — they are **never manually edited**.
If a wiki page is wrong, the source `knowledge` records are corrected and the page is
recompiled. This is Karpathy's source-of-truth rule: the database is truth, the wiki is
a derived artifact.

## Consequences

- Session startup can read compiled wiki pages for instant context without per-query retrieval
- Stale wiki pages are flagged but still readable — callers get `is_stale: true` in the response
- Wiki compilation is a separate API call, not coupled to ingest latency or reliability
- Pages accumulate across sessions — understanding compounds rather than being re-derived
- Compilation cost is paid once per explicit request, not once per ingest
- Human controls when a coherent snapshot is compiled — no partial-state wiki artifacts
- `compiled_from` (UUID[]) records which knowledge rows contributed to each wiki page,
  enabling cache invalidation: if any contributing row is superseded, the page is marked stale
