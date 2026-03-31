# OpenBrain PDF Ingestion — Eval & Test Harness Spec

**Handoff target:** Fresh Claude session  
**Repo:** `/Users/mmcmahon/src/home-lab`  
**Working dir:** `open-brain/`  
**Status:** Ready to implement — no decisions pending  
**Priority:** Run this after PDF_ingestion_spec.md is implemented

---

## Context

OpenBrain (`open-brain/`) is a family RAG system hosted on Vercel with a Supabase pgvector
backend. Text ingestion (`source_type=text`) works end-to-end with smoke tests. PDF ingestion
(`source_type=pdf`) was previously stubbed — it passed preflight and returned `status: "queued"`
without actually writing to Supabase. The implementation spec at `WIP/PDF_ingestion_spec.md`
defines the fix. This spec defines the test harness to verify the fix is correct and stays
correct over time.

The existing test infrastructure is:
- `scripts/smoke_checks.py` — 26-case HTTP smoke suite; run as `make smoke` / `make smoke-live`
- `scripts/test_query_harness.py` — 1000-query retrieval quality suite
- `scripts/test_answer_fidelity.py` — dual-judge fidelity eval (Claude Sonnet + GPT-4o)
- `scripts/eval_history.md` — shared history log appended by all harnesses

All new tests must follow the same patterns as the existing harnesses.

---

## Scope

This harness tests the full ingest → retrieve → verify loop for PDF content. It does **not**
test document layout fidelity or table extraction quality — those are out of scope for the
current pypdf implementation.

---

## What to Build

### 1. Test Fixture PDFs (`scripts/test_fixtures/pdf/`)

Create a set of minimal PDF test fixtures to use in tests. Do not embed real PII or private
content. Each fixture must be reproducible (generate programmatically or check in to the repo).

| Fixture | Purpose |
|---|---|
| `simple_text.pdf` | Single-page, clean text — baseline extraction case |
| `multi_page.pdf` | 3–5 pages — verifies page concatenation |
| `empty.pdf` | Zero text after extraction — verifies graceful handling |
| `large.pdf` | Synthetic text exceeding `OPENBRAIN_TEXT_INGEST_MAX_WORDS` — verifies chunking or rejection |
| `special_chars.pdf` | Unicode, curly quotes, em-dashes — verifies encoding handling |

Generate these programmatically with `fpdf2` or `reportlab` (dev dep, not prod). Add the
generation script at `scripts/test_fixtures/generate_pdf_fixtures.py`. Commit the generated
`.pdf` files to the repo under `scripts/test_fixtures/pdf/` so the test suite is self-contained.

### 2. Unit Tests — PDF Extraction (`scripts/test_pdf_extraction.py`)

Tests for `_extract_pdf()` in `api/_openbrain_api.py` (the extraction function added by the
implementation spec). These tests do **not** call the HTTP API or write to Supabase — they test
the extraction function in isolation.

```
test_extract_simple_text()       → text is non-empty, length > 10
test_extract_multi_page()        → output contains text from all pages
test_extract_empty_pdf()         → returns empty string or raises ExtractError (document expected behavior)
test_extract_special_chars()     → output contains expected unicode characters
test_extract_nonexistent_path()  → raises FileNotFoundError or returns empty string (document expected behavior)
```

Run with: `python scripts/test_pdf_extraction.py`  
Output format: same `PASS/FAIL` terminal pattern as `smoke_checks.py`

### 3. Smoke Tests — PDF Ingest Endpoint

Add PDF-specific cases to `scripts/smoke_checks.py`. These test the HTTP API layer:

```python
# Case: pdf ingest with valid file path — expects status "accepted", not "queued"
smoke_ingest_pdf_accepted()
  POST /ingest
  body: { source_type: "pdf", source: "<absolute path to simple_text.pdf>", owner: "mike.mcmahon67", subject: "test", topic: "pdf_smoke" }
  expect HTTP 200
  expect body.status == "accepted"   # NOT "queued"
  expect body.ingest_id is not None

# Case: pdf ingest with non-existent path — expects failure
smoke_ingest_pdf_missing_file()
  POST /ingest
  body: { source_type: "pdf", source: "/nonexistent/test.pdf", ... }
  expect HTTP 200
  expect body.status == "failed"

# Case: pdf ingest and retrieve — end-to-end
smoke_ingest_pdf_retrieval()
  1. POST /ingest with simple_text.pdf content (known unique phrase in fixture)
  2. POST /query with the unique phrase as query string
  3. expect the phrase to appear in at least one returned chunk
  NOTE: add a 1-second sleep between ingest and query to allow embedding write to settle
```

Keep the existing 26 cases intact. New cases bring the count to 29. Update the test counter
comment in `smoke_checks.py`.

### 4. Eval Harness — PDF Retrieval Quality (`scripts/test_pdf_ingest_eval.py`)

A standalone eval script that measures retrieval quality after PDF ingestion. Appends results
to `scripts/eval_history.md`.

**Structure:**

```
Phase 1 — Ingest test PDFs (3 cases)
  Ingest simple_text.pdf, multi_page.pdf, special_chars.pdf via API
  Verify each returns status "accepted"
  Record ingest_ids

Phase 2 — Query for known phrases (10 cases)
  For each ingested fixture, query for 2–3 phrases known to be in that fixture
  Score: PASS if phrase appears in top-2 results, FAIL otherwise
  Minimum pass rate: 80% (lower bar than query harness — PDF extraction is lossy)

Phase 3 — Negative queries (3 cases)
  Query for phrases NOT in any ingested fixture
  PASS if no fixture chunk appears in top-2 results

Phase 4 — Cleanup
  Delete test chunks by ingest_id from Supabase public.thoughts
  (Use direct psycopg delete, not an API endpoint — cleanup must be deterministic)
  Log how many rows deleted
```

**Output:**
```
PDF Ingest Eval — 2026-XX-XX
Total cases: 16
Passed: N/16
Pass rate: X%
Min threshold: 80%
Status: PASS / FAIL

Appended to eval_history.md: yes
```

**Env requirements:**
- `SUPABASE_DB_URL` — direct Postgres URI (for cleanup deletes)
- `OPENBRAIN_TOOL_ACCESS_TOKEN` — bearer token for API calls
- `OPENBRAIN_API_BASE` — defaults to `http://localhost:3000` for local, override for live

### 5. Cleanup Guard

The eval harness must clean up its test rows from Supabase after each run. Add a `finally`
block that always runs cleanup even if earlier phases fail. Log the number of rows deleted.
If cleanup fails, print a warning with the ingest_ids so the user can clean up manually.

```python
TEST_SUBJECT = "pdf_ingest_eval_test"
# Cleanup: DELETE FROM public.thoughts WHERE subject = TEST_SUBJECT AND owner = TEST_OWNER
```

Use `subject = "pdf_ingest_eval_test"` as the discriminator — never use a shared subject.

---

## Integration with Existing Infrastructure

### `eval_history.md` append format

```
## PDF Ingest Eval — 2026-XX-XX HH:MM
- Ingest cases: 3/3 accepted
- Retrieval cases: N/10 pass (threshold: 8/10)
- Negative cases: 3/3 pass
- Cleanup: N rows deleted
- Overall: PASS / FAIL
```

### `make` targets

Add to `open-brain/Makefile` (or create it if it doesn't exist):

```makefile
pdf-unit:
	python scripts/test_pdf_extraction.py

pdf-eval:
	python scripts/test_pdf_ingest_eval.py

pdf-eval-live:
	OPENBRAIN_API_BASE=https://openbrain-rouge.vercel.app python scripts/test_pdf_ingest_eval.py

smoke:
	python scripts/smoke_checks.py

smoke-live:
	python scripts/smoke_checks.py --live https://openbrain-rouge.vercel.app
```

---

## What NOT to Test Here

- DOCX or URL ingestion — separate scope, separate harness when those are implemented
- Table/layout extraction fidelity — pypdf is not a layout engine
- Large-file performance — not a Vercel concern at current scale
- Re-ingestion idempotency — already covered by the deterministic `ingest_id` design

---

## Files to Create

```
open-brain/scripts/test_pdf_extraction.py       ← new
open-brain/scripts/test_pdf_ingest_eval.py      ← new
open-brain/scripts/test_fixtures/pdf/           ← new directory
open-brain/scripts/test_fixtures/generate_pdf_fixtures.py  ← new
open-brain/scripts/smoke_checks.py              ← modify (add 3 cases)
open-brain/scripts/eval_history.md              ← modify (append format documented above)
open-brain/Makefile                             ← create or modify
open-brain/requirements-dev.txt                 ← add fpdf2 or reportlab
```

---

## Definition of Done

- [ ] `python scripts/test_pdf_extraction.py` — all unit tests PASS
- [ ] `make smoke` — 29/29 green (includes 3 new PDF cases)
- [ ] `make pdf-eval` — ≥80% pass rate (vs local API)
- [ ] `make pdf-eval-live` — ≥80% pass rate (vs production)
- [ ] `eval_history.md` — new entry appended
- [ ] No test rows remain in Supabase after eval run (verify with `SELECT count(*) FROM public.thoughts WHERE subject = 'pdf_ingest_eval_test'`)
- [ ] Commit to a feature branch via `/commit`, never to main
