# OpenBrain

A production RAG (Retrieval-Augmented Generation) system built to solve the AI cold-start problem: every session begins with no memory of prior decisions, architecture choices, or context. OpenBrain fixes that — ingest once, query on demand, every session starts warm.

Documented at [mikemcmahon.dev](https://mikemcmahon.dev).

---

## The Problem

AI assistants are stateless by design. Each session starts cold — no memory of what you built yesterday, what you decided last week, or why you made that architectural choice. The standard workaround is pasting context into each session manually. That doesn't scale, and it breaks continuity across long-running projects.

OpenBrain provides persistent, queryable context that travels with you across sessions, surfaces, and models.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Ingest Pipeline                    │
│   PDF · DOCX · URL · Markdown · Slack · Plain Text  │
└───────────────────────┬─────────────────────────────┘
                        │ chunk_markdown() — 600-token ceiling
                        ▼
┌─────────────────────────────────────────────────────┐
│                    Supabase                          │
│         pgvector (text-embedding-3-small)           │
│         tsvector (full-text search index)           │
│         query_log (audit trail, every call)         │
│         Row-level security per owner                │
└───────────────────────┬─────────────────────────────┘
                        │ Hybrid retrieval via RRF
                        ▼
┌─────────────────────────────────────────────────────┐
│              Retrieval (RRF fusion)                  │
│   pgvector cosine similarity                        │
│ + Postgres full-text search (tsvector)              │
│   Reciprocal Rank Fusion merges both result sets    │
└───────────────────────┬─────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────┐
│              Integration Surfaces                    │
│   Claude Code — MCP stdio server (4 tools)          │
│   Custom GPTs — OpenAPI 3.1.0 spec                 │
│   Slack — Edge Function capture                     │
│   REST API — bearer token authenticated             │
└─────────────────────────────────────────────────────┘
```

### Why hybrid retrieval

Pure vector search failed below the 90% pass rate threshold on the query harness — short documents were systematically underscored, and exact-match queries missed when embedding similarity wasn't high enough. Adding Postgres full-text search and fusing both result sets via Reciprocal Rank Fusion resolved both failure modes.

### Why Supabase over ChromaDB

ChromaDB has no web-facing API, no multi-user tenancy model, and no serverless deployment path. Supabase provides all three out of the box, plus row-level security for owner isolation and a migration-controlled schema that evolves cleanly.

---

## Measured Results

| Metric | Result | Method |
|--------|--------|--------|
| Query pass rate | 96.9% | 1,000-query harness, RRF + LengthPenalty fusion |
| Answer fidelity | 0.950 avg | Dual-judge eval (Claude Sonnet + GPT-4o independently) |
| Judge disagreement | 12% | 3 disagreements fully analyzed in baseline |
| Happy-path smoke tests | 26/26 green | Local + preview + production |
| Hallucination detection | Per-response flag | Dual-judge cross-validation |

### Eval methodology

The answer fidelity harness (`scripts/test_answer_fidelity.py`) uses two independent judges — Claude Sonnet and GPT-4o — scoring each response on retrieval accuracy, answer fidelity (0.0–1.0), and hallucination detection. Disagreements are flagged for manual review. The dual-judge design prevents any single model's biases from contaminating the baseline.

Full methodology in [`docs/EVAL_METHODOLOGY.md`](docs/EVAL_METHODOLOGY.md).

---

## Security Architecture

OpenBrain handles multi-user personal and professional context. The security layer was designed accordingly:

**Authentication**
- Bearer token auth enforced on all endpoints (`/query`, `/search`, `/ingest`, `/session_report`)
- Token → owner mapping via `OPENBRAIN_TOKEN_OWNER_MAP`
- Cross-tenant access guard: `require_auth_owner()` binds token identity to owner — mismatched owner returns 403

**Injection Defense**
- Two-layer SafeIngest gate: regex pattern check ($0.00) → optional Haiku classifier on match
- `SOCrATIC_RULES` in `tutor.py` are hardcoded Python — injected content cannot override teaching behavior regardless of gate status
- `OPENBRAIN_EXTENDED_CHECKS` toggle for additional validation

**Audit Trail**
- `public.query_log` table — every call to `query_payload` writes a row with owner, timestamp, and query content
- Foundation for behavioral analysis and anomaly detection

**Session Reporting**
- Nightly Vercel Cron (`0 3 * * *`) builds HTML session report from `query_log` and delivers via Resend API
- Recipient config via `REPORT_CONFIGS` env var (JSON array, supports multiple recipients)
- `CRON_SECRET` managed by Vercel

---

## Multi-User Tenancy

Three users, isolated namespaces. Bearer token determines owner on every request. Row-level security enforced at the Supabase layer — one user's data is structurally inaccessible via another user's token.

Each user has a dedicated integration surface:
- **Claude Code** — MCP stdio server (`docs/MCP_CONTRACT.md`)
- **Custom GPTs** — OpenAPI 3.1.0 spec with isolated bearer tokens per user (`docs/CUSTOM_GPT_ACTION_SPEC.yaml`)
- **Claude.ai connector** — HTTP MCP endpoint (`docs/CLAUDE_ACTION_SPEC.yaml`)

---

## Ingest Pipeline

Six ingest paths, all producing normalized markdown for chunking:

| Source | Handler | Notes |
|--------|---------|-------|
| Plain text / Markdown | Direct | Chunk via `chunk_markdown()` |
| DOCX | Heading-preserving extractor | Maintains document structure |
| PDF (text-layer) | Direct extraction | Skips OCR for text-dominant docs |
| PDF (scanned) | Vision OCR — Haiku/Sonnet tiered | Sonnet for handwritten (0.846 recovery rate) |
| URL / HTML | markdownify | Boilerplate stripped |
| Slack | Edge Function capture | Real-time ingest on message |

All paths route through `chunk_markdown()` with a 600-token ceiling and parent heading inheritance on sub-chunks.

---

## Deployment

Deployed as a serverless Python application on Vercel. API functions use a minimal dependency set (`requirements.txt`) to stay within Lambda install limits. Full local toolchain available via `requirements-full.txt`.

**Stack:** Python · Supabase (pgvector + PostgreSQL) · Vercel serverless · Resend (email)

---

## Development

See [`CLAUDE.md`](CLAUDE.md) for agent-facing session continuity instructions, dev workflow commands, lint/smoke targets, and repository hygiene rules.

Key docs:
- [`docs/OPENBRAIN_ARCHITECTURE.md`](docs/OPENBRAIN_ARCHITECTURE.md) — full architecture narrative
- [`docs/EVAL_METHODOLOGY.md`](docs/EVAL_METHODOLOGY.md) — eval harness design and baseline interpretation
- [`docs/AGENTS.md`](docs/AGENTS.md) — agent contract specifications
- [`docs/OPERATIONS_RUNBOOK.md`](docs/OPERATIONS_RUNBOOK.md) — day-2 operational procedures

---

## Related Projects

- **[multi-agent-lab](https://github.com/MikeMcMahon-dev/multi-agent-lab)** — multi-agent infrastructure automation with LLM-assisted failure diagnosis
- **[homelab-talos-cluster](https://github.com/MikeMcMahon-dev/homelab-talos-cluster)** — Talos Linux Kubernetes cluster (target deployment environment)
- **[mikemcmahon.dev](https://mikemcmahon.dev)** — technical blog documenting this work
