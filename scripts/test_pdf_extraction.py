#!/usr/bin/env python3
"""Unit tests for _extract_pdf() in api/_openbrain_api.py.

These tests run against fixture PDFs in isolation — no HTTP calls, no Supabase.
They WILL FAIL until _extract_pdf() is implemented in api/_openbrain_api.py.

Usage:
    python scripts/test_pdf_extraction.py

Exit code: 0 = all pass, non-zero = failures.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = PROJECT_ROOT / "scripts" / "test_fixtures" / "pdf"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Test runner helpers
# ---------------------------------------------------------------------------

_passed = 0
_failed = 0
_cases: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    global _passed, _failed
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {name}{': ' + detail if detail else ''}")
    _cases.append((name, ok, detail))
    if ok:
        _passed += 1
    else:
        _failed += 1


def _import_extract_pdf():
    """Import _extract_pdf from api._openbrain_api.

    Returns the function, or raises ImportError / AttributeError if not yet
    implemented — callers should catch and record a meaningful failure message.
    """
    from api._openbrain_api import _extract_pdf  # type: ignore[attr-defined]
    return _extract_pdf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extract_simple_text() -> None:
    """Baseline: single-page clean text PDF returns non-empty string."""
    name = "test_extract_simple_text"
    path = FIXTURE_DIR / "simple_text.pdf"
    try:
        extract = _import_extract_pdf()
        result = extract(str(path))
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        if len(result) < 10:
            _record(name, False, f"result too short ({len(result)} chars): {result!r}")
            return
        _record(name, True, f"{len(result)} chars extracted")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_pdf not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_multi_page() -> None:
    """Multi-page PDF: output contains text from all pages."""
    name = "test_extract_multi_page"
    path = FIXTURE_DIR / "multi_page.pdf"
    page_markers = [
        "Page one content begins here",
        "Page two content",
        "Page three content ends here",
    ]
    try:
        extract = _import_extract_pdf()
        result = extract(str(path))
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        missing = [m for m in page_markers if m.lower() not in result.lower()]
        if missing:
            _record(name, False, f"missing page markers: {missing}")
        else:
            _record(name, True, f"all {len(page_markers)} page markers found")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_pdf not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_empty_pdf() -> None:
    """Empty PDF (no text layer): returns empty string without raising."""
    name = "test_extract_empty_pdf"
    path = FIXTURE_DIR / "empty.pdf"
    try:
        extract = _import_extract_pdf()
        result = extract(str(path))
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        # Accept empty string or whitespace-only — the fixture has no text layer.
        stripped = result.strip()
        if stripped:
            # Some PDF readers inject page dimensions or metadata — tolerate up to 50 chars.
            if len(stripped) <= 50:
                _record(name, True, f"near-empty result ({len(stripped)} chars non-whitespace)")
            else:
                _record(name, False, f"expected empty/near-empty, got {len(stripped)} chars: {stripped[:80]!r}")
        else:
            _record(name, True, "returned empty string as expected")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_pdf not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_special_chars() -> None:
    """Special characters: output contains expected Latin-1 accented chars."""
    name = "test_extract_special_chars"
    path = FIXTURE_DIR / "special_chars.pdf"
    # These must survive extraction round-trip — all are Latin-1 range.
    expected_fragments = ["caf\u00e9", "na\u00efve", "stra\u00dfe"]
    try:
        extract = _import_extract_pdf()
        result = extract(str(path))
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        missing = [f for f in expected_fragments if f not in result]
        if missing:
            _record(name, False, f"missing fragments: {missing!r}")
        else:
            _record(name, True, f"all {len(expected_fragments)} special-char fragments found")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_pdf not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_nonexistent_path() -> None:
    """Non-existent file: raises FileNotFoundError (or returns empty string).

    Either behavior is acceptable — document which one the implementation chose.
    """
    name = "test_extract_nonexistent_path"
    path = "/nonexistent/path/does_not_exist.pdf"
    try:
        extract = _import_extract_pdf()
        result = extract(path)
        # If it returns without raising, must return str (empty is fine).
        if isinstance(result, str):
            _record(name, True, f"returned str (len={len(result)}) without raising — silent empty mode")
        else:
            _record(name, False, f"expected str or FileNotFoundError, got {type(result).__name__}")
    except FileNotFoundError:
        _record(name, True, "raised FileNotFoundError as expected")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_pdf not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"unexpected exception {type(exc).__name__}: {exc}")


def test_fixtures_exist() -> None:
    """Sanity check: all fixture PDFs are present on disk."""
    name = "test_fixtures_exist"
    expected = ["simple_text.pdf", "multi_page.pdf", "empty.pdf", "large.pdf", "special_chars.pdf"]
    missing = [f for f in expected if not (FIXTURE_DIR / f).exists()]
    if missing:
        _record(name, False, f"missing fixtures: {missing}. Run: python scripts/test_fixtures/generate_pdf_fixtures.py")
    else:
        _record(name, True, f"all {len(expected)} fixtures present")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("OpenBrain — PDF Extraction Unit Tests")
    print(f"Fixture dir: {FIXTURE_DIR}")
    print()

    test_fixtures_exist()
    test_extract_simple_text()
    test_extract_multi_page()
    test_extract_empty_pdf()
    test_extract_special_chars()
    test_extract_nonexistent_path()

    print()
    print(f"Results: {_passed}/{_passed + _failed} passed")

    if _failed:
        not_impl = sum(1 for _, ok, d in _cases if not ok and "not yet implemented" in d)
        if not_impl:
            print(
                f"NOTE: {not_impl} test(s) are blocked on _extract_pdf() implementation. "
                "Run again after api/_openbrain_api.py is updated."
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
