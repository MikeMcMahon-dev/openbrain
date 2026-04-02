# ADR-006: Structured Markdown Conversion Pipeline for All Ingestors

**Date:** 2026-04-02  
**Status:** Decided — implementation pending  
**Author:** Mike McMahon  

---

## Context

OpenBrain has four ingestor types: `markdown`, `pdf`, `docx`, and `url`. The markdown ingestor
routes through `chunk_markdown()`, which splits on headings and preserves document structure.
The other three — pdf, docx, and url — extract flat text and route through
`chunk_text_by_tokens()`: a sliding-window chunker with 500-token windows and 100-token overlap.

This means:
- DOCX heading styles (`para.style.name` = "Heading 1", "Heading 2") are discarded
- HTML heading tags (`<h1>`–`<h6>`) are stripped by `BeautifulSoup.get_text()` before chunking
- PDF structure is not preserved by `pypdf.extract_text()`
- All chunks from non-markdown sources get `heading: "root"` — no structural metadata
- 100-token overlap per chunk = ~20% token redundancy across the embedding corpus

**Triggering events:**
1. Nate's community discussion on token inefficiency from naive PDF ingestion approaches
2. Discovery that Beth McMahon's scanned PDFs (scanner output, one image per page) produce
   completely empty extraction results with no error surfaced
3. Empirical test of two real scans confirmed: both produced 0 chars extracted, 1 image/page

**The real content in those "empty" PDFs:**
- `BRN3C2AF4E06ECD_000270.pdf` (1 page): Annie's handwritten biology study notes —
  "Questions I got wrong and need to study" with question numbers, terms, and answers.
  Binomial nomenclature, taxonomy mnemonics, classification kingdoms. High-value tutor content.
- `BRN3C2AF4E06ECD_000230.pdf` (40 pages): Geometry practice test. Poor scan quality.
  Mix of geometric figures (circles, triangles, angle markers) and question text.
  Figures are integral to the questions — "Find x in the figure" requires the figure.

These are not edge cases. They are the primary use case. A "reject" path for image-dominant PDFs
would silently discard Annie's most valuable study material.

---

## Decision

### 1. PDF — Three-way classification with vision OCR for image-dominant content

PDFs are classified using `pypdf` page analysis (already a dependency). The primary signal is
average characters extracted per page. This is more reliable than empty-page percentage because
a geometry worksheet with sparse labeled text would not register as "empty" but still has far
too little text for meaningful RAG chunking.

**Classification logic:**

```python
def classify_pdf(reader) -> tuple[str, dict]:
    page_count = len(reader.pages)
    char_counts = []
    image_page_count = 0
    for page in reader.pages:
        text = page.extract_text() or ""
        char_counts.append(len(text.strip()))
        if page.images:
            image_page_count += 1
    avg_chars = sum(char_counts) / page_count if page_count else 0
    image_page_pct = image_page_count / page_count if page_count else 0
    stats = {
        "page_count": page_count,
        "avg_chars_per_page": round(avg_chars, 1),
        "image_page_pct": round(image_page_pct, 2),
    }
    if avg_chars < 50:
        return "image_dominant", stats
    elif avg_chars < 200:
        return "mixed", stats
    else:
        return "text_dominant", stats
```

**Threshold rationale:** Anchored against two real scans from Beth's scanner (0 chars/page).
The 50-char lower bound provides margin for PDFs with minimal text that are still primarily
image content. "Find the area of triangle ABC" is ~35 chars — if that's all a page has, the
figure is the substance, and the text alone is insufficient for RAG.

**Routing per class:**

| Class | avg chars/page | Route |
|---|---|---|
| `text_dominant` | ≥ 200 | `pymupdf4llm` → markdown → `chunk_markdown()` |
| `mixed` | 50–199 | `pymupdf4llm` → markdown → `chunk_markdown()` + `figures_present: true` in metadata |
| `image_dominant` | < 50 | Pre-process → vision OCR → markdown → `chunk_markdown()` |
| `image_dominant` fallback | < 50, no vision key | `status=failed`, `reason=requires_ocr_api` |

**Vision OCR pipeline for image-dominant PDFs:**

```
pymupdf (fitz) renders each page → PIL Image
  → convert to grayscale
  → ImageEnhance.Contrast(2.0) + ImageEnhance.Sharpness(1.5)
  → base64 encode → send to Claude Haiku (vision)
  → per-page markdown responses concatenated
  → set content_type="markdown"
  → chunk_markdown()
```

**Contrast/sharpness enhancement is applied universally** for image-dominant PDFs. It helps
poor-quality scans (geometry test) without hurting clean scans (handwritten notes). Parameters
are tunable via env vars (`OPENBRAIN_OCR_CONTRAST`, `OPENBRAIN_OCR_SHARPEN`) for future
adjustment without code changes.

**Vision model:** Claude Haiku. Cheap, fast, handles both handwriting and printed text well.
Falls back to `requires_ocr_api` error if `ANTHROPIC_API_KEY` is not set.

**Unified vision prompt (handles both handwritten and figure-mixed content):**

```
Transcribe all text in this scanned document page as markdown.
For any geometric diagrams, figures, or drawings, provide a brief description of what is
shown (e.g., "Circle with center O, radius labeled 5, chord AB drawn from A to B").
Preserve numbered lists, section headers, and question structure.
If text is unclear due to scan quality, transcribe your best attempt and mark
uncertain words with [?].
```

This prompt handles Annie's handwriting (transcribes text directly) and the geometry test
(transcribes question text + describes figures) without needing to detect content type.
Geometric figure descriptions in the RAG chunks are useful — "Circle with center O, radius 5"
is retrievable when Annie asks her tutor about circle problems.

**Why not reject image-dominant PDFs:**
The initial proposal was `image_dominant → reject`. This was wrong. The most valuable content
in the pipeline — Annie's handwritten study notes — is exactly what that threshold would discard.
The reject path exists only as a fallback when no vision API key is available.

**Vercel bundle constraint:** `pymupdf4llm`, `pymupdf` (fitz), and Pillow are local ingest
dependencies only (`requirements-full.txt`). The Vercel API path (`api/_openbrain_api.py`)
retains `pypdf` for on-demand ingest. The vision OCR path is local-batch only.

### 2. DOCX — Heading-preserving markdown conversion

`python-docx` exposes `para.style.name` for each paragraph. Current implementation ignores this.

Replace flat `"\n".join(para.text ...)` with a heading-aware pass:

```python
HEADING_MAP = {
    "Heading 1": "#", "Heading 2": "##", "Heading 3": "###",
    "Heading 4": "####", "Title": "#",
}
lines = []
for para in doc.paragraphs:
    if not para.text.strip():
        continue
    prefix = HEADING_MAP.get(para.style.name, "")
    lines.append(f"{prefix} {para.text}".strip() if prefix else para.text)
text = "\n".join(lines)
```

Set `content_type="markdown"` → routes through `chunk_markdown()`.

No new dependencies. Zero Vercel bundle impact.

### 3. URL/HTML — Heading-preserving HTML-to-markdown conversion

`BeautifulSoup.get_text()` strips all HTML structure. Replace with `markdownify` (pure Python,
~15KB, no system dependencies) to convert HTML to markdown before chunking.

```python
from markdownify import markdownify as md
markdown_text = md(response.text, heading_style="ATX")
```

Set `content_type="markdown"` → routes through `chunk_markdown()`.

`markdownify` is a prod dependency (Vercel API path uses URL ingest). Add to `requirements.txt`.
Expected to be well within the 250MB Vercel bundle limit.

---

## Consequences

**Positive:**
- Heading metadata populated for all ingestor types, not just markdown
- `chunk_markdown()` eliminates 100-token overlap redundancy
- Handwritten notes and scanned docs get vision OCR instead of silent empty ingestion
- Geometric figure descriptions are embedded as text — retrievable during query
- `figures_present` metadata flag enables future query-time warnings
- DOCX change requires zero new dependencies

**Negative / risks:**
- `chunk_markdown()` has no token ceiling — a very long section without subheadings becomes
  one massive chunk. Follow-on: add a max-token sub-chunking pass. Not a blocker.
- Vision OCR adds per-page API cost (Haiku is cheap but non-zero). Acceptable for family
  use case with infrequent ingest. Would need rate limiting at scale.
- Contrast/sharpness parameters (2.0x, 1.5x) are a starting point. Very dark or very faint
  scans may need tuning. Env-var overrides allow adjustment without code changes.
- `markdownify` output quality varies by site. Nav menus, cookie banners may produce noise.
  Post-processing strip for common boilerplate patterns may be needed.
- Vision OCR quality for handwriting varies by model. Haiku is good; Sonnet is better for
  degraded or complex handwriting. Make the model configurable via `OPENBRAIN_OCR_MODEL`.

---

## Alternatives Considered

**image_dominant → reject (initial proposal):** Discards Annie's study notes. Wrong for this
use case. Rejected.

**Tesseract/pytesseract for OCR:** Requires system Tesseract install, not Vercel compatible,
and produces poor results on handwritten content. Rejected.

**80% empty-page threshold (original proposal):** Too high and the wrong signal. A geometry
worksheet could have text on every page but avg <50 chars/page. Rejected.

**`marker` instead of `pymupdf4llm` for text-dominant PDFs:** Better on complex layouts but
requires ML model download (~500MB+). Overkill for study notes and handouts. Rejected.

**OCR via Google Vision / AWS Textract:** Viable but adds vendor dependency, API cost
complexity, and IAM/credentials overhead. Claude Haiku is already in the stack via the
existing OpenRouter/Anthropic key. Rejected for now.

**Separate prompts for handwriting vs. figures:** Would require content-type detection before
OCR. Unified prompt handles both adequately. Can specialize later if quality data demands it.

---

## Implementation Plan

Three parallel workstreams, then a shared test pass:

**Agent 1 — DOCX + URL (no new prod deps)**
- `scripts/ingestors/docx.py`: heading-aware extraction, `content_type="markdown"`
- `scripts/ingestors/url.py`: `markdownify` conversion, `content_type="markdown"`
- `requirements.txt`: add `markdownify`

**Agent 2 — PDF classification + pymupdf4llm + vision OCR (local path only)**
- `scripts/ingestors/pdf.py`: add `classify_pdf()`, route text/mixed through `pymupdf4llm`,
  image_dominant through Pillow pre-process + Claude Haiku vision OCR
- `requirements-full.txt`: add `pymupdf4llm`, `Pillow` (if not present)
- Env vars: `OPENBRAIN_OCR_CONTRAST` (default 2.0), `OPENBRAIN_OCR_SHARPEN` (default 1.5),
  `OPENBRAIN_OCR_MODEL` (default claude-haiku-4-5-20251001)
- Retain `pypdf` in `requirements.txt` for Vercel API path (no change to `_openbrain_api.py`)

**Agent 3 — Token efficiency A/B test harness**
- Extend `scripts/test_pdf_ingest_eval.py` with A/B comparison phase
- Path A: `pypdf` flat → `chunk_text_by_tokens` (current baseline)
- Path B: classification → appropriate route (new pipeline)
- Metrics: chunk count, heading coverage %, retrieval precision, estimated embedding tokens
- Run against: one text-dominant PDF (Annie study guide), BRN3C2AF4E06ECD_000270.pdf
  (handwritten biology notes), BRN3C2AF4E06ECD_000230.pdf (geometry test, poor quality scan)

**Shared validation:**
- Smoke suite: existing 26/26 must stay green
- New cases: PDF classification smoke, vision OCR smoke (mock Haiku if no key in CI)
- `make smoke-live` against Vercel preview before merge
- `chunk_markdown()` max-token guard: follow-on task, not a blocker for this ADR
