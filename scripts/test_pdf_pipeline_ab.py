"""
A/B comparison harness for PDF ingest pipelines.

Path A: pypdf flat text → chunk_text_by_tokens() (current baseline)
Path B: pypdf classification → pymupdf4llm markdown → chunk_markdown()
        image_dominant: vision OCR stubbed (requires ANTHROPIC_API_KEY + live API call)

No Supabase connections. No HTTP calls. Standalone.

Run from open-brain/:
    python scripts/test_pdf_pipeline_ab.py

References: ADR-006 (docs/decisions/ADR-006-structured-markdown-ingest-pipeline.md)
"""

import sys
from datetime import date
from pathlib import Path

# ---------------------------------------------------------------------------
# Path resolution — resolve chunkers relative to this script's location
# ---------------------------------------------------------------------------
SCRIPTS_DIR = Path(__file__).parent
REPO_ROOT = SCRIPTS_DIR.parent

sys.path.insert(0, str(REPO_ROOT))

from scripts.chunking.text import chunk_text_by_tokens  # noqa: E402
from scripts.chunking.markdown import chunk_markdown  # noqa: E402

# ---------------------------------------------------------------------------
# Optional dependency: pymupdf4llm
# ---------------------------------------------------------------------------
try:
    import pymupdf4llm  # type: ignore
    PYMUPDF4LLM_AVAILABLE = True
except ImportError:
    PYMUPDF4LLM_AVAILABLE = False

from pypdf import PdfReader  # noqa: E402

# ---------------------------------------------------------------------------
# PDF classification logic (from ADR-006)
# ---------------------------------------------------------------------------

def classify_pdf(reader: PdfReader) -> tuple[str, dict]:
    """
    Returns (classification, stats).

    classification:
      - 'image_dominant'  avg_chars_per_page < 50
      - 'mixed'           50 <= avg_chars_per_page < 200
      - 'text_dominant'   avg_chars_per_page >= 200
    """
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


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------

def count_tokens_whitespace(text: str) -> int:
    return len(text.split())


def heading_coverage_pct(chunks: list[dict]) -> float:
    """Percentage of chunks where heading != 'root'."""
    if not chunks:
        return 0.0
    non_root = sum(1 for c in chunks if c.get("heading", "root") != "root")
    return round(100.0 * non_root / len(chunks), 1)


def overlap_redundancy_pct(chunks: list[dict], overlap: int = 100) -> float:
    """
    Path A only. Estimates the percentage of total tokens that are redundant
    due to sliding-window overlap.

    Formula: (overlap * chunk_count) / total_tokens * 100
    """
    if not chunks:
        return 0.0
    total_tokens = sum(count_tokens_whitespace(c["text"]) for c in chunks)
    if total_tokens == 0:
        return 0.0
    redundant = overlap * len(chunks)
    return round(100.0 * redundant / total_tokens, 1)


def avg_chunk_tokens(chunks: list[dict]) -> float:
    if not chunks:
        return 0.0
    totals = [count_tokens_whitespace(c["text"]) for c in chunks]
    return round(sum(totals) / len(totals), 1)


# ---------------------------------------------------------------------------
# Path A: pypdf flat text → chunk_text_by_tokens
# ---------------------------------------------------------------------------

def run_path_a(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    text_parts = []
    for page in reader.pages:
        extracted = page.extract_text() or ""
        if extracted.strip():
            text_parts.append(extracted.strip())

    text = "\n".join(text_parts)
    extracted_chars = len(text)

    chunks = chunk_text_by_tokens(text, max_tokens=500, overlap=100) if text else []

    return {
        "extracted_chars": extracted_chars,
        "chunk_count": len(chunks),
        "heading_coverage_pct": heading_coverage_pct(chunks),
        "overlap_redundancy_pct": overlap_redundancy_pct(chunks, overlap=100),
        "avg_chunk_tokens": avg_chunk_tokens(chunks),
        "has_content": len(chunks) > 0,
    }


# ---------------------------------------------------------------------------
# Path B: classify → pymupdf4llm markdown → chunk_markdown (or OCR stub)
# ---------------------------------------------------------------------------

def run_path_b(pdf_path: Path) -> dict:
    reader = PdfReader(str(pdf_path))
    classification, stats = classify_pdf(reader)

    result = {
        "classification": classification,
        "page_count": stats["page_count"],
        "avg_chars_per_page": stats["avg_chars_per_page"],
        "image_page_pct": stats["image_page_pct"],
    }

    if classification == "image_dominant":
        page_count = stats["page_count"]
        result.update({
            "ocr_stub": True,
            "ocr_stub_message": (
                f"OCR STUB: would call Claude Haiku vision for {page_count} page(s). "
                f"Each page → base64 PNG → Haiku vision → markdown. "
                f"Requires ANTHROPIC_API_KEY."
            ),
            "extracted_chars": None,
            "chunk_count": None,
            "heading_coverage_pct": None,
            "overlap_redundancy_pct": "0% (heading-split, no overlap)",
            "avg_chunk_tokens": None,
            "has_content": "YES (pending OCR)",
        })
        return result

    # text_dominant or mixed — attempt pymupdf4llm
    if not PYMUPDF4LLM_AVAILABLE:
        result.update({
            "ocr_stub": False,
            "pymupdf4llm_unavailable": True,
            "extracted_chars": None,
            "chunk_count": None,
            "heading_coverage_pct": None,
            "overlap_redundancy_pct": "0% (heading-split, no overlap)",
            "avg_chunk_tokens": None,
            "has_content": "SKIP (pymupdf4llm not installed — pip install pymupdf4llm)",
        })
        return result

    # pymupdf4llm is available — convert to markdown and chunk
    try:
        markdown_text = pymupdf4llm.to_markdown(str(pdf_path))
    except Exception as exc:
        result.update({
            "error": str(exc),
            "extracted_chars": 0,
            "chunk_count": 0,
            "heading_coverage_pct": 0.0,
            "overlap_redundancy_pct": "0% (heading-split, no overlap)",
            "avg_chunk_tokens": 0.0,
            "has_content": False,
        })
        return result

    extracted_chars = len(markdown_text)
    chunks = chunk_markdown(markdown_text) if markdown_text else []

    result.update({
        "ocr_stub": False,
        "pymupdf4llm_unavailable": False,
        "extracted_chars": extracted_chars,
        "chunk_count": len(chunks),
        "heading_coverage_pct": heading_coverage_pct(chunks),
        "overlap_redundancy_pct": "0% (heading-split, no overlap)",
        "avg_chunk_tokens": avg_chunk_tokens(chunks),
        "has_content": len(chunks) > 0,
    })
    return result


# ---------------------------------------------------------------------------
# Table printing helpers
# ---------------------------------------------------------------------------

COL_WIDTH_LABEL = 22
COL_WIDTH_A = 20
COL_WIDTH_B = 28


def fmt_row(label: str, a_val, b_val) -> str:
    return (
        f"  {label:<{COL_WIDTH_LABEL}}"
        f"{str(a_val):<{COL_WIDTH_A}}"
        f"{str(b_val)}"
    )


def fmt_pct(val) -> str:
    if val is None:
        return "OCR STUB"
    if isinstance(val, str):
        return val
    return f"{val}%"


def fmt_chars(val) -> str:
    if val is None:
        return "OCR STUB"
    return str(val)


def fmt_count(val) -> str:
    if val is None:
        return "OCR STUB"
    return str(val)


def fmt_has_content(val) -> str:
    if isinstance(val, str):
        return val
    return "YES" if val else "NO"


def print_pdf_table(label: str, path_a: dict, path_b: dict) -> None:
    print(f"\n--- {label} ---")
    print(
        f"  {'':22}"
        f"{'Path A (current)':<{COL_WIDTH_A}}"
        f"Path B (new)"
    )

    classification_a = "n/a"
    classification_b = path_b.get("classification", "n/a")
    print(fmt_row("Classification", classification_a, classification_b))

    extracted_a = path_a["extracted_chars"]
    extracted_b = path_b.get("extracted_chars")
    print(fmt_row("Extracted chars", extracted_a, fmt_chars(extracted_b)))

    chunk_a = path_a["chunk_count"]
    chunk_b = path_b.get("chunk_count")
    print(fmt_row("Chunk count", chunk_a, fmt_count(chunk_b)))

    hdg_a = "n/a"  # Path A always produces heading=root
    hdg_b = path_b.get("heading_coverage_pct")
    if hdg_b is None and path_b.get("classification") == "image_dominant":
        hdg_b_str = "OCR STUB"
    elif hdg_b is None:
        hdg_b_str = "n/a"
    else:
        hdg_b_str = f"{hdg_b}%"
    print(fmt_row("Heading coverage", hdg_a, hdg_b_str))

    ored_a = fmt_pct(path_a["overlap_redundancy_pct"])
    ored_b = path_b.get("overlap_redundancy_pct", "0% (heading-split, no overlap)")
    print(fmt_row("Overlap redundancy", ored_a, ored_b))

    avgt_a = path_a["avg_chunk_tokens"]
    avgt_b = path_b.get("avg_chunk_tokens")
    avgt_b_str = "OCR STUB" if avgt_b is None and path_b.get("classification") == "image_dominant" else (str(avgt_b) if avgt_b is not None else "SKIP")
    print(fmt_row("Avg chunk tokens", avgt_a, avgt_b_str))

    has_a = fmt_has_content(path_a["has_content"])
    has_b = fmt_has_content(path_b["has_content"])
    print(fmt_row("Has content", has_a, has_b))

    # Surface notes
    if path_b.get("ocr_stub"):
        print(f"\n  [Path B note] {path_b['ocr_stub_message']}")
    if path_b.get("pymupdf4llm_unavailable"):
        print(f"\n  [Path B note] pymupdf4llm not installed — install with: pip install pymupdf4llm")
    if path_b.get("error"):
        print(f"\n  [Path B error] {path_b['error']}")


# ---------------------------------------------------------------------------
# Test fixture definitions
# ---------------------------------------------------------------------------

FIXTURES = [
    {
        "filename": "BRN3C2AF4E06ECD_000270.pdf",
        "description": "1 page, handwritten biology notes",
    },
    {
        "filename": "BRN3C2AF4E06ECD_000230.pdf",
        "description": "40 pages, geometry test, poor scan quality",
    },
]

FIXTURES_DIR = REPO_ROOT / "test-fixtures"


def find_additional_text_dominant_pdf() -> Path | None:
    """
    Scan test-fixtures/ and scripts/test_fixtures/pdf/ for any PDF not already
    in FIXTURES that pypdf can extract text from (avg >= 200 chars/page).
    Returns the best match (highest avg chars), or None.
    """
    known = {f["filename"] for f in FIXTURES}
    search_dirs = [FIXTURES_DIR, REPO_ROOT / "scripts" / "test_fixtures" / "pdf"]
    best: tuple[float, Path] | None = None
    for search_dir in search_dirs:
        for pdf_path in sorted(search_dir.glob("*.pdf")):
            if pdf_path.name in known:
                continue
            try:
                reader = PdfReader(str(pdf_path))
                chars = [len((p.extract_text() or "").strip()) for p in reader.pages]
                avg = sum(chars) / len(chars) if chars else 0
                if avg >= 200 and (best is None or avg > best[0]):
                    best = (avg, pdf_path)
            except Exception:
                continue
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=== PDF Pipeline A/B Comparison ===")
    print(f"Date: {date.today()}")

    pymupdf_status = "available" if PYMUPDF4LLM_AVAILABLE else "NOT INSTALLED (pip install pymupdf4llm)"
    print(f"\nDependencies:")
    print(f"  pypdf:        available")
    print(f"  pymupdf4llm:  {pymupdf_status}")

    all_results: list[tuple[str, dict, dict]] = []
    ocr_stub_count = 0

    # Check for an extra text-dominant baseline PDF
    baseline_pdf = find_additional_text_dominant_pdf()
    extra_fixtures = []
    if baseline_pdf:
        extra_fixtures.append({
            "filename": baseline_pdf.name,
            "full_path": baseline_pdf,
            "description": "additional baseline (text-dominant)",
        })
    else:
        print(
            "\n[Note] No additional text-dominant PDF found. "
            "Only running against the two scan fixtures."
        )

    run_fixtures = FIXTURES + extra_fixtures

    for fixture in run_fixtures:
        pdf_path = fixture.get("full_path") or FIXTURES_DIR / fixture["filename"]
        if not pdf_path.exists():
            print(f"\n[SKIP] {fixture['filename']} — file not found at {pdf_path}")
            continue

        path_a = run_path_a(pdf_path)
        path_b = run_path_b(pdf_path)
        all_results.append((fixture["filename"], fixture["description"], path_a, path_b, pdf_path))

    # Print tables
    for filename, description, path_a, path_b, _pdf_path in all_results:
        print_pdf_table(f"{filename} ({description})", path_a, path_b)

    # Summary
    print("\n=== SUMMARY ===")

    path_a_total_chunks = sum(r[2]["chunk_count"] for r in all_results)
    path_b_definite_chunks = sum(
        r[3]["chunk_count"]
        for r in all_results
        if isinstance(r[3].get("chunk_count"), int)
    )
    path_b_pending_ocr = sum(
        1 for r in all_results if r[3].get("ocr_stub") is True
    )
    ocr_stub_count = sum(
        r[3]["page_count"]
        for r in all_results
        if r[3].get("ocr_stub") is True
    )

    path_b_heading_covered = [
        r[3]["heading_coverage_pct"]
        for r in all_results
        if isinstance(r[3].get("heading_coverage_pct"), (int, float))
    ]
    heading_avg = (
        round(sum(path_b_heading_covered) / len(path_b_heading_covered), 1)
        if path_b_heading_covered
        else "n/a (all pending OCR or unavailable)"
    )

    path_a_total_tokens = sum(
        round(r[2]["avg_chunk_tokens"] * r[2]["chunk_count"])
        for r in all_results
        if isinstance(r[2].get("chunk_count"), int) and r[2]["chunk_count"] > 0
    )
    path_a_overlap_tokens = sum(
        100 * r[2]["chunk_count"] for r in all_results
        if isinstance(r[2].get("chunk_count"), int)
    )

    pending_str = f" (+ {path_b_pending_ocr} doc(s) / {ocr_stub_count} page(s) pending OCR)" if path_b_pending_ocr else ""

    print(f"Path A total chunks across all docs: {path_a_total_chunks}")
    print(f"Path B total chunks across all docs: {path_b_definite_chunks}{pending_str}")
    heading_avg_str = f"{heading_avg}%" if isinstance(heading_avg, (int, float)) else str(heading_avg)
    print(f"Path B heading coverage (where measured): {heading_avg_str}")
    print(f"Path A estimated overlap-redundant tokens: ~{path_a_overlap_tokens} tokens")
    print(f"  (100-token overlap x {path_a_total_chunks} chunks)")
    if path_b_definite_chunks > 0 or path_b_pending_ocr > 0:
        print(f"Path B overlap redundancy: 0 tokens (heading-split chunking, no overlap)")
    print()
    if path_b_pending_ocr:
        print(
            f"[OCR stub] {path_b_pending_ocr} image-dominant PDF(s) require vision OCR to produce chunks. "
            f"Set ANTHROPIC_API_KEY and run the full pipeline (pdf.py) to activate."
        )
    if not PYMUPDF4LLM_AVAILABLE:
        print(
            "[pymupdf4llm] Path B text/mixed extraction requires pymupdf4llm. "
            "Install with: pip install pymupdf4llm"
        )


if __name__ == "__main__":
    main()
