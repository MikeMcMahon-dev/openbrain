#!/usr/bin/env python3
"""Generate PDF test fixtures for the OpenBrain PDF ingest test harness.

Usage:
    python scripts/test_fixtures/generate_pdf_fixtures.py

Requires: fpdf2 (dev dependency)
Output: scripts/test_fixtures/pdf/*.pdf
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent / "pdf"

# Unique phrase embedded in simple_text.pdf — used for retrieval smoke test.
# Do not change without updating smoke_checks.py and test_pdf_ingest_eval.py.
SIMPLE_TEXT_UNIQUE_PHRASE = "xyloquartz-retrieval-fixture-alpha"

# Known phrases embedded per fixture — used by eval harness.
# Keys must match fixture filenames (without .pdf).
FIXTURE_KNOWN_PHRASES: dict[str, list[str]] = {
    "simple_text": [
        SIMPLE_TEXT_UNIQUE_PHRASE,
        "The quick brown fox jumps over the lazy dog",
    ],
    "multi_page": [
        "Page one content begins here for the multi-page fixture",
        "Page three content ends here for the multi-page fixture",
    ],
    "special_chars": [
        "caf\u00e9 na\u00efve resum\u00e9",
        "stra\u00dfe M\u00fcnchen",
    ],
}


def _require_fpdf() -> None:
    try:
        import fpdf  # noqa: F401
    except ImportError:
        print("ERROR: fpdf2 is required to generate fixtures.")
        print("Install with: pip install -r requirements-dev.txt")
        sys.exit(1)


def generate_simple_text(out_dir: Path) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(
        0,
        8,
        f"OpenBrain PDF Test Fixture: simple_text\n\n"
        f"Unique retrieval phrase: {SIMPLE_TEXT_UNIQUE_PHRASE}\n\n"
        f"The quick brown fox jumps over the lazy dog. "
        f"This is a single-page PDF with clean ASCII text. "
        f"It serves as the baseline extraction case for the OpenBrain test harness.",
    )
    path = out_dir / "simple_text.pdf"
    pdf.output(str(path))
    return path


def generate_multi_page(out_dir: Path) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pages = [
        "Page one content begins here for the multi-page fixture. "
        "This is the first page of a three-page PDF. "
        "Retrieval harness expects text from all pages to be present after extraction.",
        "Page two content for the multi-page fixture. "
        "Middle pages must not be silently dropped by the extraction layer. "
        "Each page contributes unique text to verify concatenation.",
        "Page three content ends here for the multi-page fixture. "
        "If this text appears in a query result the extractor handled all pages correctly. "
        "End of multi-page test fixture.",
    ]
    for page_text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.multi_cell(0, 8, page_text)

    path = out_dir / "multi_page.pdf"
    pdf.output(str(path))
    return path


def generate_empty(out_dir: Path) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    # No text added — page is blank.
    path = out_dir / "empty.pdf"
    pdf.output(str(path))
    return path


def generate_large(out_dir: Path) -> Path:
    from fpdf import FPDF

    # Default OPENBRAIN_TEXT_INGEST_MAX_WORDS is 6000 — generate ~7000 words.
    word = "infrastructure"
    sentence = " ".join([word] * 14) + ". "  # 14 words + period
    block = sentence * 10  # ~140 words per block
    body = block * 52  # ~7280 words total

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=10)
    # fpdf2 multi_cell handles page breaks automatically
    pdf.multi_cell(0, 6, f"OpenBrain PDF Test Fixture: large\n\n{body}")
    path = out_dir / "large.pdf"
    pdf.output(str(path))
    return path


def generate_special_chars(out_dir: Path) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    # Helvetica is Latin-1; use Latin-1 accented chars + ASCII stand-ins for
    # higher-plane chars (em-dash -> --, curly quotes -> straight quotes).
    # The goal is to verify the extractor handles non-ASCII Latin-1 without
    # crashing, not to round-trip every Unicode code point.
    pdf.set_font("Helvetica", size=12)
    content = (
        "OpenBrain PDF Test Fixture: special_chars\n\n"
        "French: caf\u00e9 na\u00efve resum\u00e9\n"
        "German: stra\u00dfe M\u00fcnchen \u00fcber\n"
        "Spanish: ma\u00f1ana cami\u00f3n a\u00f1o\n"
        "Punctuation: em--dash and \"straight quotes\"\n"
        "Math: 100\u00b0 angle, pi approx 3.14159\n"
        "Currency: GBP\u00a350, YEN 500\n"
    )
    pdf.multi_cell(0, 8, content)
    path = out_dir / "special_chars.pdf"
    pdf.output(str(path))
    return path


GENERATORS = {
    "simple_text": generate_simple_text,
    "multi_page": generate_multi_page,
    "empty": generate_empty,
    "large": generate_large,
    "special_chars": generate_special_chars,
}


def main() -> int:
    _require_fpdf()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    failed = 0
    for name, fn in GENERATORS.items():
        try:
            path = fn(OUT_DIR)
            size = os.path.getsize(path)
            print(f"PASS  {name}.pdf ({size:,} bytes) -> {path}")
        except Exception as exc:
            print(f"FAIL  {name}.pdf: {exc}")
            failed += 1

    if failed:
        print(f"\n{failed} fixture(s) failed to generate.")
    else:
        print(f"\nAll {len(GENERATORS)} fixtures generated in {OUT_DIR}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
