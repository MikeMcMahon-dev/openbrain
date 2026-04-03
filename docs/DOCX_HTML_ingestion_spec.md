# OpenBrain DOCX + URL Ingestion — Implementation Spec

**Handoff target:** Fresh Claude session  
**Repo:** `/Users/mmcmahon/src/home-lab`  
**Working dir:** `open-brain/`  
**Status:** Decisions made — ready to implement  
**Suggested model:** `claude-haiku-4-5-20251001` — this is pattern-following work; if output quality is insufficient fall back to `claude-sonnet-4-6`  
**Prerequisite:** `WIP/PDF_ingestion_spec.md` must be implemented first — this spec follows the identical pattern and references it

---

## Context

OpenBrain is a family RAG system on Vercel + Supabase pgvector. Text and PDF ingestion are live.
`source_type=docx` and `source_type=url` are stubbed — they return `status: "queued"` and never
write to Supabase. This spec implements both.

The PDF implementation (already done) is the exact template for this work. Read
`api/_openbrain_api.py` to see how `_extract_pdf()` and its wiring into `ingest_payload()` were
done, then replicate the pattern for DOCX and URL.

---

## Decisions Made — Do Not Re-Open

### DOCX: python-docx

`python-docx` is already proven in `scripts/ingestors/docx.py` in this codebase.
Pure Python, no system dependencies, compatible with Vercel's bundle constraints.
Add `python-docx` to `requirements.txt` pinned to a specific version.

### URL: stdlib only (no new dependencies)

The existing `scripts/ingestors/url.py` uses `requests` + `BeautifulSoup`, but neither is in
`requirements.txt`. The Vercel API layer (`_openbrain_api.py`) already uses `urllib.request`
throughout (see `_source_reachable()`). The decision is to keep the API layer dependency-free:

- Fetch with `urllib.request` — already in use, no new dep
- Strip HTML with `html.parser` via stdlib `html.HTMLParser` — no BeautifulSoup needed
- Add `User-Agent: openbrain-ingester/1.0` header — precedent from the Resend Cloudflare fix

Do not use `requests`, `httpx`, or `beautifulsoup4` in the API implementation.

---

## Implementation Steps

### Step 1 — Add python-docx to requirements.txt

`open-brain/requirements.txt` currently contains:
```
psycopg[binary]==3.2.10
pypdf==<version>
```

Add:
```
python-docx==<latest stable version>
```

Check PyPI for the current pinned version before writing.

### Step 2 — Add `_extract_docx()` to `api/_openbrain_api.py`

Add alongside `_extract_pdf()`. Pattern is identical:

```python
def _extract_docx(source: str) -> str:
    """Extract text from a DOCX file path. Returns joined paragraph text."""
    from docx import Document  # local import — only loaded when needed
    try:
        doc = Document(source)
        parts = [
            para.text.strip()
            for para in doc.paragraphs
            if para.text and para.text.strip()
        ]
    except Exception as exc:
        raise ValueError(f"DOCX extraction failed for {source}: {exc}") from exc
    return "\n".join(parts)
```

Notes:
- Local import pattern — consistent with `_extract_pdf()`
- Raise `ValueError` on failure — caller maps to `status="failed"`
- Empty document returns empty string — caller handles via the empty-check guard

### Step 3 — Add `_fetch_url()` to `api/_openbrain_api.py`

```python
def _fetch_url(source: str) -> str:
    """Fetch a URL and return stripped plain text. Uses stdlib only."""
    import html as _html
    from html.parser import HTMLParser

    class _TextExtractor(HTMLParser):
        SKIP_TAGS = {"script", "style", "head", "noscript"}

        def __init__(self):
            super().__init__()
            self._parts: list[str] = []
            self._skip_depth = 0

        def handle_starttag(self, tag, attrs):
            if tag.lower() in self.SKIP_TAGS:
                self._skip_depth += 1

        def handle_endtag(self, tag):
            if tag.lower() in self.SKIP_TAGS:
                self._skip_depth = max(0, self._skip_depth - 1)

        def handle_data(self, data):
            if self._skip_depth == 0:
                stripped = data.strip()
                if stripped:
                    self._parts.append(stripped)

        def get_text(self) -> str:
            return "\n".join(self._parts)

    request = urllib.request.Request(
        source,
        headers={"User-Agent": "openbrain-ingester/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            charset = "utf-8"
            content_type = response.headers.get("Content-Type", "")
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip().split(";")[0].strip()
            raw_html = response.read().decode(charset, errors="replace")
    except Exception as exc:
        raise ValueError(f"URL fetch failed for {source}: {exc}") from exc

    parser = _TextExtractor()
    parser.feed(raw_html)
    return _html.unescape(parser.get_text())
```

Notes:
- `urllib` is already imported at the top of `_openbrain_api.py` — confirm before adding imports
- The `_TextExtractor` class can be defined inline or as a module-level private class — either is fine
- `html.unescape()` handles `&amp;`, `&nbsp;`, etc.
- 15-second timeout — generous for slow sites, bounded for Vercel's function timeout

### Step 4 — Wire both into `ingest_payload()`

Read the PDF implementation in `ingest_payload()` — it's the `elif source_type == "pdf":` block.
Add two identical blocks immediately after it, before the final `else:` stub:

```python
        elif source_type == "docx":
            # identical structure to pdf block — _extract_docx() in place of _extract_pdf()
            # same word-count guard, same chunking logic, same status/message pattern

        elif source_type == "url":
            # identical structure to pdf block — _fetch_url() in place of _extract_pdf()
            # same word-count guard, same chunking logic, same status/message pattern
            # note: _source_reachable() already validated the URL before reaching this branch
```

Do not copy the code literally from this spec — read the actual PDF block in the file and
replicate its exact pattern for DOCX and URL. This ensures variable names, indentation, and
the `compute_ingest_id` / `_write_text_ingest` calls stay consistent with what's already there.

### Step 5 — Update `open-brain/docs/OPENBRAIN_NEXT_STEPS.md`

Find the URGENT section. After updating it to reflect PDF as done, mark DOCX and URL as
implemented. Add a note:

> URL ingestion uses stdlib urllib + html.parser (no requests/BeautifulSoup). Content quality
> depends on site structure — navigation/boilerplate text will be included. Not suitable for
> JavaScript-rendered pages (no headless browser). Sufficient for documentation and article URLs.

---

## Validation

Follow the same sequence as the PDF implementation:

```bash
# 1. Quick import check
cd open-brain && source .venv/bin/activate
python -c "from api._openbrain_api import _extract_docx, _fetch_url; print('OK')"

# 2. Local smoke — expect 26/26 (or whatever PDF brought it to), no regressions
python scripts/smoke_checks.py

# 3. Deploy to Vercel preview
vercel

# 4. Live smoke against preview
python scripts/smoke_checks.py --live https://<preview-url>.vercel.app

# 5. Manual DOCX test
curl -X POST https://<preview-url>.vercel.app/ingest \
  -H "Authorization: Bearer $OPENBRAIN_TOOL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type":"docx","source":"/path/to/test.docx","owner":"mike.mcmahon67","subject":"test","topic":"docx_validation"}'
# Expect: status "accepted"

# 6. Manual URL test
curl -X POST https://<preview-url>.vercel.app/ingest \
  -H "Authorization: Bearer $OPENBRAIN_TOOL_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"source_type":"url","source":"https://example.com","owner":"mike.mcmahon67","subject":"test","topic":"url_validation"}'
# Expect: status "accepted"

# 7. Query to verify both wrote to Supabase
# Use a phrase known to be in each test document/page

# 8. Merge to main only after live smoke passes
```

---

## Files to Modify

```
open-brain/requirements.txt              ← add python-docx==<version>
open-brain/api/_openbrain_api.py         ← add _extract_docx(), _fetch_url(), wire into ingest_payload()
open-brain/docs/OPENBRAIN_NEXT_STEPS.md  ← mark DOCX + URL implemented, add URL limitations note
```

No new files needed.

---

## Definition of Done

- [ ] `requirements.txt` contains `python-docx==<pinned version>`
- [ ] `_extract_docx()` implemented in `_openbrain_api.py`
- [ ] `_fetch_url()` implemented in `_openbrain_api.py` using stdlib only
- [ ] `ingest_payload()` handles `source_type=docx` — returns `status="accepted"`, not `"queued"`
- [ ] `ingest_payload()` handles `source_type=url` — returns `status="accepted"`, not `"queued"`
- [ ] Empty DOCX returns `status="failed"` with clear message
- [ ] Unreachable URL returns `status="failed"` (already handled by `_source_reachable()`)
- [ ] `make smoke` — all cases green, no regressions
- [ ] `make smoke-live` against preview — all cases green
- [ ] Manual curl tests confirm `status="accepted"` for both types
- [ ] Content is retrievable via `/query` after ingest
- [ ] `OPENBRAIN_NEXT_STEPS.md` updated
- [ ] Commit to feature branch via `/commit`, never to main
- [ ] PR opened
- [ ] Once `WIP/DOCX_HTML_ingestion_testing.md` is also implemented and eval passes,
  run `git mv` for both WIP spec files into `open-brain/docs/` in a single commit
