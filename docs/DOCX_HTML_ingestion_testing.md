# OpenBrain DOCX + URL Ingestion — Eval & Test Harness Spec

**Handoff target:** Fresh Claude session  
**Repo:** `/Users/mmcmahon/src/home-lab`  
**Working dir:** `open-brain/`  
**Status:** Ready to implement after `WIP/DOCX_HTML_ingestion_spec.md` is done  
**Suggested model:** `claude-haiku-4-5-20251001` — pattern-following work; fall back to `claude-sonnet-4-6` if needed  
**Reference:** `WIP/PDF_ingestion_testing.md` defines the identical pattern — read it first

---

## Context

The PDF ingestion test harness (`WIP/PDF_ingestion_testing.md`) defined the test pattern for
this system. This spec extends that pattern to DOCX and URL ingestion. The structure —
fixtures, unit tests, smoke cases, eval harness, cleanup guard — is identical. Read the PDF
testing spec before starting; do not duplicate its explanatory context here.

Existing test infrastructure to build on:
- `scripts/smoke_checks.py` — HTTP smoke suite
- `scripts/test_pdf_extraction.py` — PDF unit tests (reference for DOCX unit test structure)
- `scripts/test_pdf_ingest_eval.py` — PDF eval harness (reference for DOCX/URL eval structure)
- `scripts/test_fixtures/pdf/` — PDF fixtures (reference for fixture generation approach)
- `scripts/eval_history.md` — shared history log

---

## DOCX Test Work

### Fixtures (`scripts/test_fixtures/docx/`)

Generate programmatically with `python-docx` (already a prod dep after the impl spec).
Add generation to the existing `scripts/test_fixtures/generate_pdf_fixtures.py` — rename it
to `generate_fixtures.py` and add a DOCX section, or create
`scripts/test_fixtures/generate_docx_fixtures.py` as a parallel script.

| Fixture | Purpose |
|---|---|
| `simple_text.docx` | Single paragraph, clean text — baseline |
| `multi_paragraph.docx` | 10+ paragraphs — verifies all are joined |
| `empty.docx` | No paragraphs — verifies graceful empty handling |
| `special_chars.docx` | Unicode, em-dashes, curly quotes |
| `large.docx` | Synthetic text exceeding `OPENBRAIN_TEXT_INGEST_MAX_WORDS` — verifies chunking |

Commit generated `.docx` files to the repo.

### Unit Tests (`scripts/test_docx_extraction.py`)

Mirror `scripts/test_pdf_extraction.py` exactly. Test `_extract_docx()` in isolation:

```
test_extract_simple_text()       → text is non-empty
test_extract_multi_paragraph()   → all paragraphs present in output
test_extract_empty_docx()        → returns empty string (no raise)
test_extract_special_chars()     → unicode characters preserved
test_extract_nonexistent_path()  → raises ValueError (python-docx raises on missing file)
```

Run with: `python scripts/test_docx_extraction.py`

### Smoke Cases (add to `scripts/smoke_checks.py`)

Add 3 DOCX cases following the PDF smoke pattern:

```
smoke_ingest_docx_accepted()     → POST /ingest, source_type=docx, valid path → status "accepted"
smoke_ingest_docx_missing_file() → POST /ingest, source_type=docx, bad path → status "failed"
smoke_ingest_docx_retrieval()    → ingest → query for known phrase → phrase in top-2 results
```

### Eval Cases (add DOCX phase to `scripts/test_docx_url_ingest_eval.py`)

See structure below.

---

## URL Test Work

### Fixtures

URL tests use live URLs — no file fixtures needed. Use stable, public, text-heavy URLs:

| URL | Purpose |
|---|---|
| `https://example.com` | Minimal HTML — baseline, always reachable |
| `https://en.wikipedia.org/wiki/Kubernetes` | Real content, known phrases, stable |
| A non-existent URL (e.g., `https://example.com/nonexistent-path-404`) | Verifies failure handling |

Do not use URLs that require authentication, JavaScript rendering, or that may go offline.
Wikipedia is the safest choice for content retrieval tests.

### Unit Tests (`scripts/test_url_fetch.py`)

Test `_fetch_url()` in isolation:

```
test_fetch_example_com()         → text is non-empty, contains "Example Domain"
test_fetch_strips_html_tags()    → no raw HTML tags (<div>, <script>, etc.) in output
test_fetch_unescape_entities()   → &amp; → &, &nbsp; stripped or replaced
test_fetch_bad_url()             → raises ValueError
test_fetch_404()                 → raises ValueError (urllib raises HTTPError on 404)
```

Run with: `python scripts/test_url_fetch.py`

### Smoke Cases (add to `scripts/smoke_checks.py`)

Add 3 URL cases:

```
smoke_ingest_url_accepted()      → POST /ingest, source_type=url, valid URL → status "accepted"
smoke_ingest_url_bad_url()       → POST /ingest, source_type=url, invalid URL → status "failed"
smoke_ingest_url_retrieval()     → ingest example.com → query "Example Domain" → in top-2 results
```

Note on `smoke_ingest_url_retrieval()`: use `https://example.com` — it's a 1-paragraph page,
extraction is deterministic, "Example Domain" is a unique phrase unlikely to collide with
existing brain content.

---

## Combined Eval Harness (`scripts/test_docx_url_ingest_eval.py`)

A single eval script covering both source types. Follows the same 4-phase structure as
`scripts/test_pdf_ingest_eval.py`. Appends to `scripts/eval_history.md`.

```
Phase 1 — DOCX Ingest (3 cases)
  Ingest simple_text.docx, multi_paragraph.docx, special_chars.docx
  Verify each returns status "accepted"

Phase 2 — DOCX Retrieval (8 cases)
  Query for known phrases from each fixture
  Pass: phrase in top-2 results
  Minimum pass rate: 75% (6/8) — DOCX extraction is clean but chunking may affect recall

Phase 3 — URL Ingest (2 cases)
  Ingest https://example.com and the Wikipedia Kubernetes article
  Verify each returns status "accepted"

Phase 4 — URL Retrieval (4 cases)
  Query for "Example Domain" → expect example.com chunk in top-2
  Query for 3 phrases known to be in the Kubernetes Wikipedia article
  Minimum pass rate: 75% (3/4)

Phase 5 — Negative queries (3 cases)
  Query for phrases not in any test fixture or Wikipedia article
  Pass: no test-subject chunk in top-2

Phase 6 — Cleanup
  DELETE FROM public.thoughts WHERE subject = 'docx_url_ingest_eval_test'
  Log rows deleted; warn with IDs on failure
```

**Test subject discriminator:** `subject = "docx_url_ingest_eval_test"` — never share with other tests.

**Env requirements:** same as `test_pdf_ingest_eval.py`:
- `SUPABASE_DB_URL`
- `OPENBRAIN_TOOL_ACCESS_TOKEN`
- `OPENBRAIN_API_BASE` (default: `http://localhost:3000`)

**Output format:** same as PDF eval:
```
DOCX + URL Ingest Eval — 2026-XX-XX
DOCX cases: N/11
URL cases: N/6
Negative cases: 3/3
Overall pass rate: X%
Min threshold: 75%
Status: PASS / FAIL

Appended to eval_history.md: yes
```

**`eval_history.md` append format:**
```
## DOCX + URL Ingest Eval — 2026-XX-XX HH:MM
- DOCX ingest: 3/3 accepted
- DOCX retrieval: N/8 pass (threshold: 6/8)
- URL ingest: 2/2 accepted
- URL retrieval: N/4 pass (threshold: 3/4)
- Negative cases: 3/3 pass
- Cleanup: N rows deleted
- Overall: PASS / FAIL
```

---

## Makefile Additions

Add to `open-brain/Makefile` alongside the PDF targets:

```makefile
docx-unit:
	python scripts/test_docx_extraction.py

url-unit:
	python scripts/test_url_fetch.py

docx-url-eval:
	python scripts/test_docx_url_ingest_eval.py

docx-url-eval-live:
	OPENBRAIN_API_BASE=https://openbrain-rouge.vercel.app python scripts/test_docx_url_ingest_eval.py
```

---

## Files to Create / Modify

```
open-brain/scripts/test_docx_extraction.py           ← new
open-brain/scripts/test_url_fetch.py                 ← new
open-brain/scripts/test_docx_url_ingest_eval.py      ← new
open-brain/scripts/test_fixtures/docx/               ← new directory
open-brain/scripts/test_fixtures/generate_docx_fixtures.py  ← new (or extend generate_pdf_fixtures.py)
open-brain/scripts/smoke_checks.py                   ← modify (add 6 cases)
open-brain/scripts/eval_history.md                   ← modify (append)
open-brain/Makefile                                  ← modify (add targets)
open-brain/requirements-dev.txt                      ← no change needed (python-docx is now prod dep)
```

---

## Definition of Done

- [ ] `python scripts/test_docx_extraction.py` — all unit tests PASS
- [ ] `python scripts/test_url_fetch.py` — all unit tests PASS
- [ ] `make smoke` — all cases green including 6 new DOCX + URL cases
- [ ] `make docx-url-eval` — ≥75% pass rate (local)
- [ ] `make docx-url-eval-live` — ≥75% pass rate (production)
- [ ] `eval_history.md` — new entry appended
- [ ] No test rows remain in Supabase after eval run
- [ ] Commit to feature branch via `/commit`, never to main
- [ ] PR opened
- [ ] Once this spec and `WIP/DOCX_HTML_ingestion_spec.md` are both implemented and eval passes,
  `git mv` both WIP files to `open-brain/docs/` in a single commit
