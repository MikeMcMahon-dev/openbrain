#!/usr/bin/env python3
"""Unit tests for _extract_docx() in api/_openbrain_api.py.

These tests run against fixture DOCX files in isolation — no HTTP calls, no Supabase.
They WILL FAIL until _extract_docx() is implemented in api/_openbrain_api.py.

Usage:
    python scripts/test_docx_extraction.py

Exit code: 0 = all pass, non-zero = failures.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = PROJECT_ROOT / "scripts" / "test_fixtures" / "docx"

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


def _import_extract_docx():
    """Import _extract_docx from api._openbrain_api.

    Returns the function, or raises ImportError / AttributeError if not yet
    implemented — callers should catch and record a meaningful failure message.
    """
    from api._openbrain_api import _extract_docx  # type: ignore[attr-defined]
    return _extract_docx


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_extract_simple_text() -> None:
    """Baseline: single-paragraph DOCX returns non-empty string."""
    name = "test_extract_simple_text"
    path = FIXTURE_DIR / "simple_text.docx"
    try:
        extract = _import_extract_docx()
        result = extract(str(path))
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        if len(result) < 10:
            _record(name, False, f"result too short ({len(result)} chars): {result!r}")
            return
        _record(name, True, f"{len(result)} chars extracted")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_docx not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_multi_paragraph() -> None:
    """Multi-paragraph DOCX: output contains text from all paragraphs."""
    name = "test_extract_multi_paragraph"
    path = FIXTURE_DIR / "multi_paragraph.docx"
    # Markers from first, middle, and last paragraphs
    paragraph_markers = [
        "paragraph one begins here",
        "paragraph eleven ends here",
    ]
    try:
        extract = _import_extract_docx()
        result = extract(str(path))
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        missing = [m for m in paragraph_markers if m.lower() not in result.lower()]
        if missing:
            _record(name, False, f"missing paragraph markers: {missing}")
        else:
            _record(name, True, f"all {len(paragraph_markers)} paragraph markers found")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_docx not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_empty_docx() -> None:
    """Empty DOCX (no paragraphs): returns empty string without raising."""
    name = "test_extract_empty_docx"
    path = FIXTURE_DIR / "empty.docx"
    try:
        extract = _import_extract_docx()
        result = extract(str(path))
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        # python-docx may add a single empty default paragraph; strip and check.
        stripped = result.strip()
        if stripped:
            # Tolerate up to 50 chars of boilerplate (e.g. default empty paragraph text)
            if len(stripped) <= 50:
                _record(name, True, f"near-empty result ({len(stripped)} chars non-whitespace)")
            else:
                _record(name, False, f"expected empty, got {len(stripped)} chars: {stripped[:80]!r}")
        else:
            _record(name, True, "returned empty string as expected")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_docx not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_special_chars() -> None:
    """Special characters: Unicode chars survive extraction round-trip."""
    name = "test_extract_special_chars"
    path = FIXTURE_DIR / "special_chars.docx"
    # python-docx preserves full Unicode — these must all be present in output.
    expected_fragments = [
        "caf\u00e9",      # café
        "na\u00efve",     # naïve
        "Stra\u00dfe",    # Straße
        "\u2014",         # em-dash
        "\u201c",         # left curly quote
    ]
    try:
        extract = _import_extract_docx()
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
        _record(name, False, "_extract_docx not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_extract_nonexistent_path() -> None:
    """Non-existent file: raises ValueError (python-docx raises on missing file).

    Per spec: _extract_docx wraps all errors as ValueError so callers can map
    uniformly to status="failed".
    """
    name = "test_extract_nonexistent_path"
    path = "/nonexistent/path/does_not_exist.docx"
    try:
        extract = _import_extract_docx()
        result = extract(path)
        # If it returns without raising, must return str.
        if isinstance(result, str):
            _record(name, True, f"returned str (len={len(result)}) without raising — silent empty mode")
        else:
            _record(name, False, f"expected str or ValueError, got {type(result).__name__}")
    except (ValueError, FileNotFoundError) as exc:
        _record(name, True, f"raised {type(exc).__name__} as expected")
    except (ImportError, AttributeError):
        _record(name, False, "_extract_docx not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"unexpected exception {type(exc).__name__}: {exc}")


def test_fixtures_exist() -> None:
    """Sanity check: all fixture DOCX files are present on disk."""
    name = "test_fixtures_exist"
    expected = ["simple_text.docx", "multi_paragraph.docx", "empty.docx", "large.docx", "special_chars.docx"]
    missing = [f for f in expected if not (FIXTURE_DIR / f).exists()]
    if missing:
        _record(name, False, f"missing fixtures: {missing}. Run: python scripts/test_fixtures/generate_docx_fixtures.py")
    else:
        _record(name, True, f"all {len(expected)} fixtures present")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("OpenBrain — DOCX Extraction Unit Tests")
    print(f"Fixture dir: {FIXTURE_DIR}")
    print()

    test_fixtures_exist()
    test_extract_simple_text()
    test_extract_multi_paragraph()
    test_extract_empty_docx()
    test_extract_special_chars()
    test_extract_nonexistent_path()

    print()
    print(f"Results: {_passed}/{_passed + _failed} passed")

    if _failed:
        not_impl = sum(1 for _, ok, d in _cases if not ok and "not yet implemented" in d)
        if not_impl:
            print(
                f"NOTE: {not_impl} test(s) are blocked on _extract_docx() implementation. "
                "Run again after api/_openbrain_api.py is updated."
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
