#!/usr/bin/env python3
"""Unit tests for _fetch_url() in api/_openbrain_api.py.

These tests use live URLs (example.com) — requires network access.
They WILL FAIL until _fetch_url() is implemented in api/_openbrain_api.py.

Usage:
    python scripts/test_url_fetch.py

Exit code: 0 = all pass, non-zero = failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

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


def _import_fetch_url():
    """Import _fetch_url from api._openbrain_api.

    Returns the function, or raises ImportError / AttributeError if not yet
    implemented — callers should catch and record a meaningful failure message.
    """
    from api._openbrain_api import _fetch_url  # type: ignore[attr-defined]
    return _fetch_url


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_fetch_example_com() -> None:
    """Baseline: https://example.com returns non-empty text containing 'Example Domain'."""
    name = "test_fetch_example_com"
    url = "https://example.com"
    try:
        fetch = _import_fetch_url()
        result = fetch(url)
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        if len(result) < 10:
            _record(name, False, f"result too short ({len(result)} chars): {result!r}")
            return
        if "Example Domain" not in result:
            _record(name, False, f"'Example Domain' not found in result. Got: {result[:200]!r}")
            return
        _record(name, True, f"{len(result)} chars, 'Example Domain' present")
    except (ImportError, AttributeError):
        _record(name, False, "_fetch_url not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_fetch_strips_html_tags() -> None:
    """HTML tags must be stripped: no <div>, <script>, <p> etc. in output."""
    name = "test_fetch_strips_html_tags"
    url = "https://example.com"
    # Tags that must not appear in cleaned output
    forbidden_patterns = ["<div", "<script", "<head", "<body", "<html", "<p>", "</p>"]
    try:
        fetch = _import_fetch_url()
        result = fetch(url)
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        found = [p for p in forbidden_patterns if p.lower() in result.lower()]
        if found:
            _record(name, False, f"raw HTML tags found in output: {found}")
        else:
            _record(name, True, "no raw HTML tags in output")
    except (ImportError, AttributeError):
        _record(name, False, "_fetch_url not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_fetch_unescape_entities() -> None:
    """HTML entities (&amp;, &nbsp;, etc.) must be unescaped or stripped."""
    name = "test_fetch_unescape_entities"
    url = "https://example.com"
    try:
        fetch = _import_fetch_url()
        result = fetch(url)
        if not isinstance(result, str):
            _record(name, False, f"expected str, got {type(result).__name__}")
            return
        # Raw &amp; should not appear in output — it should be unescaped to &
        if "&amp;" in result:
            _record(name, False, "raw '&amp;' entity found — html.unescape() not applied")
            return
        # Raw &nbsp; should not appear as the literal string
        if "&nbsp;" in result:
            _record(name, False, "raw '&nbsp;' entity found in output")
            return
        _record(name, True, "no raw HTML entities in output")
    except (ImportError, AttributeError):
        _record(name, False, "_fetch_url not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"{type(exc).__name__}: {exc}")


def test_fetch_bad_url() -> None:
    """Malformed URL: raises ValueError."""
    name = "test_fetch_bad_url"
    url = "not-a-valid-url"
    try:
        fetch = _import_fetch_url()
        result = fetch(url)
        # Should have raised — if it returns, check type
        if isinstance(result, str):
            _record(name, False, f"expected ValueError, returned str: {result[:80]!r}")
        else:
            _record(name, False, f"expected ValueError, returned {type(result).__name__}")
    except ValueError as exc:
        _record(name, True, f"raised ValueError as expected: {exc}")
    except (ImportError, AttributeError):
        _record(name, False, "_fetch_url not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"unexpected exception {type(exc).__name__}: {exc}")


def test_fetch_404() -> None:
    """404 URL: urllib raises HTTPError → wrapped as ValueError."""
    name = "test_fetch_404"
    url = "https://example.com/nonexistent-path-404"
    try:
        fetch = _import_fetch_url()
        result = fetch(url)
        # example.com may return a custom 404 page with text instead of raising.
        # Accept either: raised ValueError, or returned text (non-empty str).
        if isinstance(result, str):
            _record(name, True, f"returned str ({len(result)} chars) — server returned body on 404")
        else:
            _record(name, False, f"expected ValueError or str, got {type(result).__name__}")
    except ValueError as exc:
        _record(name, True, f"raised ValueError as expected: {exc}")
    except (ImportError, AttributeError):
        _record(name, False, "_fetch_url not yet implemented in api/_openbrain_api.py")
    except Exception as exc:
        _record(name, False, f"unexpected exception {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("OpenBrain — URL Fetch Unit Tests")
    print("Note: requires network access to https://example.com")
    print()

    test_fetch_example_com()
    test_fetch_strips_html_tags()
    test_fetch_unescape_entities()
    test_fetch_bad_url()
    test_fetch_404()

    print()
    print(f"Results: {_passed}/{_passed + _failed} passed")

    if _failed:
        not_impl = sum(1 for _, ok, d in _cases if not ok and "not yet implemented" in d)
        if not_impl:
            print(
                f"NOTE: {not_impl} test(s) are blocked on _fetch_url() implementation. "
                "Run again after api/_openbrain_api.py is updated."
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
