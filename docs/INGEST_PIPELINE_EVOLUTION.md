# OpenBrain Ingest Pipeline Evolution: From Flat Text to Structured Markdown

**Project:** OpenBrain (family RAG system)  
**Date:** 2026-04-02  
**Status:** Decision made, implementation in progress  
**Tags:** RAG, embeddings, token efficiency, PDF ingestion, chunking strategy, vision OCR  

---

## The Problem We Didn't Know We Had

OpenBrain has been ingesting documents for months. Study notes, PDFs, web pages, Word docs —
all going into Supabase pgvector, all queryable. It worked. Annie's tutor was finding relevant
content. The retrieval eval was passing at >96%.

Then Nate dropped a video about token burn from naive PDF ingestion approaches, and I went and
looked at the actual code.

Every non-markdown ingestor was doing the same thing:

1. Extract text (ignoring all document structure)
2. Throw the flat blob at a sliding-window chunker: 500 tokens, 100 overlap
3. Embed each chunk — every one labeled `heading: "root"`

The structure was sitting right there. `python-docx` exposes heading styles. `BeautifulSoup`
has `<h1>` through `<h6>`. And we were throwing all of it away before the first token counted.

---

## What "Structure Discarded" Actually Costs

### DOCX: The heading data was right there

```python
# What we were doing
text = "\n".join(para.text for para in doc.paragraphs if para.text.strip())

# What para.style.name actually contains, unused
# "Heading 1", "Heading 2", "Heading 3", "Normal", "Title"...
```

A 20-page study guide with clear chapter headings gets chunked like it has no chapters.
Retrieval has to work harder. Chunks from "Chapter 3: Congruent Triangles" and "Chapter 7:
Circle Theorems" look identical in the metadata — both say `heading: "root"`.

### HTML/URL: `get_text()` is a structure shredder

```python
# What we were doing
text = soup.get_text("\n", strip=True)
```

`get_text()` walks the whole DOM and concatenates everything. The `<h2>` that said
"Installation" and the `<h2>` that said "Configuration" both become anonymous text blobs.

### Sliding window: the 20% redundancy tax

With `max_tokens=500` and `overlap=100`, every chunk shares 100 tokens with its neighbor.
At scale, ~20% of every embedded chunk is content already embedded in the previous chunk.
You're paying embedding API cost for repeated context.

The markdown chunker (`chunk_markdown()`) doesn't overlap at all — it splits at heading
boundaries. Natural document structure provides the context boundary.

### PDF: The problem had two shapes, and the worse one was invisible

**Shape 1 — Text-dominant PDFs:** Same problem as DOCX/HTML. `pypdf.extract_text()` produces
flat text. Structure that existed in the original document is gone.

**Shape 2 — Scanned PDFs:** This one was silent and worse.

Beth scans Annie's study materials on our home scanner. The scanner produces image-per-page
PDFs. `pypdf.extract_text()` returns empty string. The ingest pipeline would have silently
written zero chunks to the database with no error surfaced.

Two real scans confirmed the failure mode:

```
BRN3C2AF4E06ECD_000270.pdf — 1 page
  pypdf result: 0 chars, 1 image
  Actual content: Annie's handwritten biology study notes
  "Questions I got wrong and need to study"
  → terms, definitions, taxonomy mnemonics, Kingdom classification

BRN3C2AF4E06ECD_000230.pdf — 40 pages
  pypdf result: 0 chars per page, 1 image per page
  Actual content: Geometry practice test (poor scan quality)
  → mix of geometric figures and question text
  → figures are integral to the questions, not decorative
```

These are not edge cases. They are the primary use case for the ingest pipeline. Scanned study
materials are what Beth and Annie are actually submitting.

---

## Why "Just Reject Scanned PDFs" Was Wrong

The first instinct was: detect scanned PDFs, return `status=failed` with `requires_ocr`, make
the problem someone else's problem.

That would have discarded Annie's handwritten biology notes — the most valuable thing in the
pipeline.

The right answer is to handle it. And the tooling to handle it is already in the stack.

---

## The Decision

### Unify around markdown as the internal format

All ingestors now produce markdown before chunking. Markdown is the common language that
`chunk_markdown()` already knows how to handle. The chunker splits on heading boundaries,
preserves section context, and doesn't pad with overlap.

This isn't "convert everything to a different format" — it's "stop throwing away the structure
you already have."

### DOCX: one pass, zero new dependencies

Map `python-docx`'s `para.style.name` to markdown heading prefixes. No new packages.

### URL/HTML: one package, full structure preserved

`markdownify` (pure Python, ~15KB) converts HTML to ATX-style markdown. `<h2>Installation</h2>`
becomes `## Installation`. One package in `requirements.txt`.

### PDF: three-way classification + vision OCR for image-dominant content

PDFs require a classification step because they're fundamentally different objects depending
on how they were created. The primary signal: average characters extracted per page by `pypdf`.

```
avg < 50 chars/page   → image_dominant  → vision OCR pipeline
avg 50–199 chars/page → mixed           → pymupdf4llm + figures_present flag
avg ≥ 200 chars/page  → text_dominant   → pymupdf4llm → markdown
```

Why avg chars and not empty-page percentage? Because a geometry worksheet might have text on
every page but only 30-40 chars of it ("Find x. Show your work."). Empty-page count would miss
that. Average chars is a more stable signal for "is this actually text-extractable?"

The 50-char threshold was anchored against real data. Annie's scanner output averaged 0 chars
across both test documents. A threshold of 50 gives room for incidental text artifacts while
still routing genuinely sparse documents to OCR.

### Vision OCR: pre-process first, then Haiku

For image-dominant PDFs:

```
pymupdf renders each page → PIL Image
  → grayscale
  → contrast 2.0x (helps degraded scans without hurting clean ones)
  → sharpness 1.5x
  → base64 → Claude Haiku
  → per-page markdown → concatenate → chunk_markdown()
```

The geometry test has poor scan quality — the source material was low-contrast to begin with.
The contrast enhancement runs universally for image-dominant PDFs; it doesn't degrade clean
handwritten scans and materially helps degraded printed ones.

**The vision prompt is unified across content types:**

> Transcribe all text in this scanned document page as markdown. For any geometric diagrams,
> figures, or drawings, provide a brief description of what is shown (e.g., "Circle with
> center O, radius labeled 5, chord AB drawn from A to B"). Preserve numbered lists,
> section headers, and question structure. If text is unclear due to scan quality, transcribe
> your best attempt and mark uncertain words with [?].

This handles Annie's handwriting (transcribes it directly) and the geometry test (transcribes
question text AND describes the figures) without needing to detect which type is present.

**Why describe figures instead of skipping them?**

A geometry question that says "Find x in the figure below" is useless without the figure. But
"Circle with center O, radius labeled 5, chord AB" is retrievable. When Annie asks her tutor
about circle chord problems, that description will surface the right content.

Figure descriptions aren't a consolation prize — they're the right format for RAG.

---

## The Architecture: Before and After

### Before

```
PDF    ──→ pypdf.extract_text() ──→ flat blob ──→ chunk_text_by_tokens ──→ heading="root"
DOCX   ──→ para.text join       ──→ flat blob ──→ chunk_text_by_tokens ──→ heading="root"
URL    ──→ soup.get_text()      ──→ flat blob ──→ chunk_text_by_tokens ──→ heading="root"
MD     ──→ read file            ──→ markdown  ──→ chunk_markdown()     ──→ heading=<real>

Scanned PDF  ──→ 0 chars ──→ 0 chunks ──→ silent empty ingest
```

### After

```
PDF (text)   ──→ classify → pymupdf4llm   ──→ markdown ──→ chunk_markdown() ──→ heading=<real>
PDF (mixed)  ──→ classify → pymupdf4llm   ──→ markdown ──→ chunk_markdown() ──→ figures_present=true
PDF (image)  ──→ classify → contrast+sharpen → Haiku vision → markdown → chunk_markdown()
PDF (no key) ──→ classify → status=failed, reason=requires_ocr_api
DOCX         ──→ heading-aware pass       ──→ markdown ──→ chunk_markdown() ──→ heading=<real>
URL          ──→ markdownify              ──→ markdown ──→ chunk_markdown() ──→ heading=<real>
MD           ──→ read file                ──→ markdown ──→ chunk_markdown() ──→ heading=<real>
```

---

## What We're Testing

Token efficiency A/B comparison (`scripts/test_pdf_ingest_eval.py`):

- **Path A:** `pypdf` flat → `chunk_text_by_tokens` (current baseline)
- **Path B:** classification → appropriate route (new pipeline)

Test documents:
- A text-dominant Annie study PDF
- `BRN3C2AF4E06ECD_000270.pdf` — handwritten biology notes (image_dominant)
- `BRN3C2AF4E06ECD_000230.pdf` — geometry test, poor quality (image_dominant)

Metrics per path:
- Chunk count per document
- Heading coverage % (chunks with non-"root" heading)
- Retrieval precision at k=2 for known phrases
- Estimated total embedding token count

Results will be appended here once the harness runs.

---

## What's Still Open

- `chunk_markdown()` has no token ceiling. A section without subheadings that runs 2000 words
  becomes a single chunk. Needs a max-token sub-chunking pass. Follow-on work.
- OCR model is configurable (`OPENBRAIN_OCR_MODEL`, default claude-haiku-4-5-20251001). For
  difficult handwriting, claude-sonnet-4-6 is materially better. Worth evaluating on Annie's
  actual scans once the pipeline is running.
- Contrast/sharpness parameters (2.0x, 1.5x) are env-var overridable. The geometry test is
  the hardest case — may need tuning after first run.
- `markdownify` on boilerplate-heavy sites. Nav menus, cookie banners, footers may produce
  noise in chunks. Post-processing strip for common patterns may be needed.

---

*Decision record: [ADR-006](decisions/ADR-006-structured-markdown-ingest-pipeline.md)*  
*Implementation: three parallel agent workstreams — see ADR-006 Implementation Plan*  
*Draft for: mikemcmahon.dev — RAG engineering / AI systems category*
