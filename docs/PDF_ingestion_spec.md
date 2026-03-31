# OpenBrain PDF Ingestion — Implementation Spec

**Handoff target:** Fresh Claude session  
**Repo:** `/Users/mmcmahon/src/home-lab`  
**Working dir:** `open-brain/`  
**Status:** Decision made — ready to implement  
**Follow-on:** After this is done, implement `WIP/PDF_ingestion_testing.md`

---

## Context

OpenBrain (`open-brain/`) is a family RAG system on Vercel + Supabase pgvector. Text ingestion
(`source_type=text`) is live and working. `source_type=pdf`, `docx`, and `url` were stubbed —
they pass preflight checks and return `status: "queued"` but never write to Supabase. PDFs that
Annie's ChatGPT uploaded last week were silently dropped.

This spec implements PDF ingestion. DOCX and URL can be done in the same session if time allows,
but PDF is the priority.

---

## Decision: pypdf (made — do not re-open)

**Use `pypdf` for PDF text extraction.**

Rationale:
- Already imported in `scripts/ingestors/pdf.py` — proven to work in this codebase
- Pure Python, no system dependencies — compatible with Vercel's bundle constraints
- Vercel serverless functions have a 250MB compressed bundle limit; pdfplumber adds ~5MB of
  pdfminer.six deps without meaningful benefit for this use case
- The content being ingested is study notes and document text (text-dominant PDFs, not tables)
- pypdf handles Unicode, multi-page, and the realistic inputs this system will see

Do not use pdfplumber, pymupdf, or any library that requires system-level dependencies.

---

## Architectural Overview

The ingest entry point is `ingest_payload()` in `api/_openbrain_api.py`. For `source_type=text`,
it calls `_write_text_ingest()`. For `pdf`, `docx`, `url`, it currently falls to the `else`
branch (~line 1448) and returns `status="queued"`.

The fix is:
1. Add `_extract_pdf(source: str) -> str` to `api/_openbrain_api.py`
2. Add `pypdf` to `requirements.txt`
3. Replace the `else` stub in `ingest_payload()` with extraction + `_write_text_ingest()` calls

The `source` field for `source_type=pdf` is a **file path** (absolute or relative) to the PDF
on disk. The existing `_source_reachable()` already validates `Path(source).exists()` for
non-url, non-obsidian types — so path validation is already handled.

---

## ChatGPT Action Interface (important context)

The ChatGPT actions cannot send raw binary files. For PDF uploads from ChatGPT:
- The GPT **must extract text from the uploaded PDF and submit as `source_type=text`**
- The `source_type=pdf` path is for **server-side batch ingestion scripts**

This is not a regression — it's the correct design. The Annie GPT's system prompt should be
updated to instruct it to extract PDF text and submit as `source_type=text`. That update is
out of scope for this spec but should be noted in OPENBRAIN_NEXT_STEPS.md.

The original "silent drop" happened because the GPT called ingest with `source_type=pdf` and
a filename. That content was never recoverable (ChatGPT does not expose uploaded file content
via API after the fact). Re-ingestion requires Annie to re-upload the PDFs.

---

## Implementation Steps

### Step 1 — Add pypdf to requirements.txt

`open-brain/requirements.txt` currently contains only:
```
psycopg[binary]==3.2.10
```

Add:
```
pypdf==5.1.0
```

Pin to a specific version. At time of writing, 5.x is the stable series and matches the
import style used in `scripts/ingestors/pdf.py`. Confirm the current latest patch version
before writing (`pip index versions pypdf` or check PyPI).

### Step 2 — Add `_extract_pdf()` to `api/_openbrain_api.py`

Find the section of `_openbrain_api.py` that contains `_write_text_ingest()` and add the
extraction function nearby. Read the file first to find the exact insertion point.

```python
def _extract_pdf(source: str) -> str:
    """Extract text from a PDF file path. Returns empty string if extraction fails."""
    from pypdf import PdfReader  # local import — only loaded when needed
    path = Path(source)
    text_parts = []
    try:
        reader = PdfReader(str(path))
        for page in reader.pages:
            extracted = page.extract_text() or ""
            if extracted.strip():
                text_parts.append(extracted.strip())
    except Exception as exc:
        raise ValueError(f"PDF extraction failed for {source}: {exc}") from exc
    return "\n".join(text_parts)
```

Notes:
- Use a local import (`from pypdf import PdfReader` inside the function) so that the module
  is only loaded when PDF ingestion is actually invoked. This is consistent with the lazy-import
  pattern and avoids import errors if pypdf is not installed in a non-Vercel dev environment.
- Raise `ValueError` on extraction failure so the caller can map it to a `status="failed"` response.
- Return empty string if all pages produce no text (scanned/image-only PDF).

### Step 3 — Wire extraction into `ingest_payload()`

Read `api/_openbrain_api.py` around line 1448 (the `else` stub). The current structure is:

```python
        else:
            preflight_summary = _ingest_preflight(owner, _tenant_id, source, source_type)
            if preflight_summary.get("status") == "failed":
                status = "failed"
                message = "Ingest blocked by pre-flight checks."
                details.extend(preflight_summary.get("errors", []))
            else:
                status = "queued"
                message = "Ingest request accepted. Processing is currently queued in the MCP scaffold."
                if preflight_summary.get("warnings"):
                    details.extend(preflight_summary.get("warnings", []))
```

Replace the `else` branch with:

```python
        elif source_type == "pdf":
            try:
                extracted_text = _extract_pdf(source)
            except ValueError as exc:
                status = "failed"
                message = f"Ingest failed: {exc}"
                details.append(str(exc))
            else:
                if not extracted_text.strip():
                    status = "failed"
                    message = "Ingest failed: PDF contained no extractable text (may be image-only)."
                    details.append("empty extraction")
                else:
                    # Apply the same word-count guard as text ingest
                    word_count = len(extracted_text.split())
                    max_words = int(os.getenv("OPENBRAIN_TEXT_INGEST_MAX_WORDS", "6000"))
                    if word_count > max_words:
                        # Chunk into sections and ingest each separately
                        words = extracted_text.split()
                        chunk_size = 1500
                        chunks = [
                            " ".join(words[i:i + chunk_size])
                            for i in range(0, len(words), chunk_size)
                        ]
                        failed_chunks = []
                        for idx, chunk in enumerate(chunks):
                            chunk_id = compute_ingest_id(source_type, chunk, owner, subject, f"{topic}_chunk{idx}")
                            write_error = _write_text_ingest(chunk, owner, _tenant_id, subject, f"{topic}_chunk{idx}", chunk_id)
                            if write_error:
                                failed_chunks.append(idx)
                        if failed_chunks:
                            status = "failed"
                            message = f"Ingest failed: {len(failed_chunks)}/{len(chunks)} chunks failed to write."
                        else:
                            status = "accepted"
                            message = f"Ingest accepted. PDF split into {len(chunks)} chunks."
                            details.append(f"chunks: {len(chunks)}, words: {word_count}")
                    else:
                        _pdf_ingest_id = compute_ingest_id(source_type, extracted_text, owner, subject, topic)
                        write_error = _write_text_ingest(extracted_text, owner, _tenant_id, subject, topic, _pdf_ingest_id)
                        if write_error:
                            status = "failed"
                            message = f"Ingest failed: {write_error}"
                            details.append(write_error)
                        else:
                            status = "accepted"
                            message = "Ingest request accepted."
        else:
            # DOCX, URL, and other source types remain queued until implemented
            preflight_summary = _ingest_preflight(owner, _tenant_id, source, source_type)
            if preflight_summary.get("status") == "failed":
                status = "failed"
                message = "Ingest blocked by pre-flight checks."
                details.extend(preflight_summary.get("errors", []))
            else:
                status = "queued"
                message = "Ingest request accepted. Processing is currently queued in the MCP scaffold."
                if preflight_summary.get("warnings"):
                    details.extend(preflight_summary.get("warnings", []))
```

**Important:** Read the file before editing. The exact indentation and surrounding variable
assignments must be preserved. The `_source_reachable()` call that gates this branch runs before
this code — do not duplicate the reachability check.

### Step 4 — Update `vercel.json` / Vercel build if needed

Vercel builds from `requirements.txt`. Adding `pypdf` there is sufficient. No `vercel.json`
changes are needed unless a build override is configured. Verify after `vercel --prod` deploy.

### Step 5 — Update `open-brain/docs/OPENBRAIN_NEXT_STEPS.md`

Find the "URGENT — Next Session" section and update:
- Mark PDF as implemented
- Add note: "ChatGPT GPT actions cannot submit raw PDFs — GPT must extract text and submit as
  `source_type=text`. Annie GPT system prompt update required (out of scope this session)."
- Add to near-term list: "Re-ingest Annie's PDFs that were silently dropped (require re-upload
  from Annie — original content is not recoverable from ChatGPT upload history)."

---

## Validation

After implementing, validate in this order:

```bash
# 1. Local unit-level validation (does extraction work at all?)
cd open-brain
source .venv/bin/activate
python -c "
from api._openbrain_api import _extract_pdf
# Create a test PDF first (or use an existing one in the repo)
print('pypdf import OK')
"

# 2. Local smoke
python scripts/smoke_checks.py
# Expect: same 26/26 green (no new smoke cases yet — those come from the test harness spec)

# 3. Deploy to Vercel preview
vercel

# 4. Live smoke against preview
python scripts/smoke_checks.py --live https://<preview-url>.vercel.app

# 5. Manual ingest test via curl (using a local PDF path — server-side only)
curl -X POST https://<preview-url>.vercel.app/ingest \
  -H "Authorization: Bearer $OPENBRAIN_TOOL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type":"pdf","source":"/path/to/test.pdf","owner":"mike.mcmahon67","subject":"test","topic":"pdf_validation"}'
# Expect: status "accepted", NOT "queued"

# 6. Query to verify write
curl -X POST https://<preview-url>.vercel.app/query \
  -H "Authorization: Bearer $OPENBRAIN_TOOL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"<phrase from the test PDF>","owner":"mike.mcmahon67","n_results":3}'
# Expect: chunk containing the phrase in results

# 7. Merge to main only after live smoke passes
```

---

## Files to Modify

```
open-brain/requirements.txt          ← add pypdf==<version>
open-brain/api/_openbrain_api.py     ← add _extract_pdf(), modify ingest_payload() else-branch
open-brain/docs/OPENBRAIN_NEXT_STEPS.md  ← update URGENT section, add Annie GPT note
```

No new files needed for the core implementation.

---

## Definition of Done

- [ ] `requirements.txt` contains `pypdf==<pinned version>`
- [ ] `_extract_pdf()` is implemented in `_openbrain_api.py`
- [ ] `ingest_payload()` handles `source_type=pdf` — returns `status="accepted"`, not `"queued"`
- [ ] Large PDFs (>6000 words) are chunked and each chunk written separately
- [ ] Empty/image-only PDFs return `status="failed"` with a clear message
- [ ] `make smoke` — 26/26 green (no regressions)
- [ ] `make smoke-live` against preview — 26/26 green
- [ ] Manual curl test confirms `status="accepted"` and content is retrievable via `/query`
- [ ] `OPENBRAIN_NEXT_STEPS.md` updated
- [ ] Commit to feature branch via `/commit`, never to main
- [ ] PR opened
- [ ] Once both this spec and `WIP/PDF_ingestion_testing.md` are fully implemented and smoke/eval
  passes end-to-end, move both files out of `WIP/`:
  ```bash
  git mv WIP/PDF_ingestion_spec.md open-brain/docs/PDF_ingestion_spec.md
  git mv WIP/PDF_ingestion_testing.md open-brain/docs/PDF_ingestion_testing.md
  ```
  Do this as a single commit after the test harness session completes — not before.
