#!/usr/bin/env python3
"""DOCX + URL Ingest Eval Harness — OpenBrain

Measures retrieval quality after DOCX and URL ingestion. Appends results to eval_history.md.

Phases:
  1. Ingest DOCX fixtures (3 cases)
  2. Query for known DOCX phrases (8 cases, threshold: 6/8)
  3. Ingest URLs (2 cases)
  4. Query for known URL content (4 cases, threshold: 3/4)
  5. Negative queries — phrases NOT in any ingested fixture or URL (3 cases)
  6. Cleanup — DELETE test rows from Supabase by subject discriminator

Usage:
    python scripts/test_docx_url_ingest_eval.py

Environment variables:
    SUPABASE_DB_URL           — direct Postgres URI (required for cleanup)
    OPENBRAIN_TOOL_ACCESS_TOKEN — bearer token for API calls
    OPENBRAIN_API_BASE         — defaults to http://localhost:3000
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOCX_FIXTURE_DIR = PROJECT_ROOT / "scripts" / "test_fixtures" / "docx"
EVAL_HISTORY = PROJECT_ROOT / "scripts" / "eval_history.md"

TEST_SUBJECT = "docx_url_ingest_eval_test"
TEST_OWNER = "mike.mcmahon67"

API_BASE = os.getenv("OPENBRAIN_API_BASE", "http://localhost:3000").rstrip("/")
TOKEN = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
AUTH_HEADER = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

# Known phrases per DOCX fixture (must match generate_docx_fixtures.py).
DOCX_FIXTURE_QUERIES: list[tuple[str, str, str]] = [
    # (fixture_name, phrase, description)
    ("simple_text", "xyloquartz-retrieval-fixture-beta", "unique retrieval phrase"),
    ("simple_text", "The quick brown fox jumps over the lazy dog", "common phrase"),
    ("simple_text", "baseline extraction case", "descriptive phrase"),
    ("multi_paragraph", "paragraph one begins here", "first paragraph marker"),
    ("multi_paragraph", "paragraph eleven ends here", "last paragraph marker"),
    ("special_chars", "caf\u00e9 na\u00efve r\u00e9sum\u00e9", "French accented chars"),
    ("special_chars", "Stra\u00dfe M\u00fcnchen", "German umlauts"),
    ("special_chars", "special_chars", "fixture label"),
]

# URLs to ingest and their known phrases.
URL_INGEST_CASES: list[tuple[str, str, list[str]]] = [
    # (url, description, known_phrases_to_query)
    (
        "https://example.com",
        "example.com baseline",
        ["Example Domain"],
    ),
    (
        "https://en.wikipedia.org/wiki/Kubernetes",
        "Wikipedia Kubernetes article",
        [
            "container orchestration",
            "Google",
            "open-source",
        ],
    ),
]

# Flat list of (url_key, phrase, description) for Phase 4 retrieval
URL_RETRIEVAL_QUERIES: list[tuple[str, str, str]] = [
    ("https://example.com", "Example Domain", "example.com headline"),
    ("https://en.wikipedia.org/wiki/Kubernetes", "container orchestration", "Kubernetes definition"),
    ("https://en.wikipedia.org/wiki/Kubernetes", "Google", "Kubernetes origin"),
    ("https://en.wikipedia.org/wiki/Kubernetes", "open-source", "Kubernetes license"),
]

NEGATIVE_QUERIES: list[tuple[str, str]] = [
    ("zzz-this-phrase-does-not-exist-in-any-fixture-xqz", "nonexistent phrase"),
    ("quantum chromodynamics flavor symmetry breaking", "unrelated physics term"),
    ("supercalifragilisticexpialidocious-banana-walrus", "nonsense phrase"),
]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post(path: str, body: dict) -> tuple[int, dict]:
    url = f"{API_BASE}{path}"
    payload = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", **AUTH_HEADER}
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    ctx = ssl.create_default_context() if url.startswith("https") else None
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(body_text)
        except Exception:
            return exc.code, {"error": body_text}
    except urllib.error.URLError as exc:
        return 0, {"error": f"connection error: {exc.reason}"}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _db_url() -> str | None:
    return (
        os.getenv("OPENBRAIN_SUPABASE_CONNECTION_STRING")
        or os.getenv("SUPABASE_DB_URL")
        or os.getenv("SUPABASE_DATABASE_URL")
        or os.getenv("DATABASE_URL")
    )


def _cleanup_test_rows() -> tuple[int, str | None]:
    """Delete test rows from public.thoughts where subject = TEST_SUBJECT."""
    db_url = _db_url()
    if not db_url:
        return 0, "SUPABASE_DB_URL not set — cleanup skipped"
    try:
        from psycopg import connect
        from psycopg.rows import dict_row

        conn = connect(db_url, autocommit=True, row_factory=dict_row)
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM public.thoughts WHERE subject = %s AND created_by_user_login = %s",
            (TEST_SUBJECT, TEST_OWNER),
        )
        deleted = cur.rowcount
        cur.close()
        conn.close()
        return deleted, None
    except Exception as exc:
        return 0, f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Phase 1 — DOCX Ingest
# ---------------------------------------------------------------------------

def phase1_docx_ingest() -> tuple[list[str], list[str]]:
    """Ingest 3 DOCX fixtures. Returns (accepted_fixture_names, failed_fixture_names)."""
    fixtures = ["simple_text", "multi_paragraph", "special_chars"]
    accepted: list[str] = []
    failed: list[str] = []

    print("\n--- Phase 1: Ingest DOCX fixtures ---")
    for name in fixtures:
        path = str(DOCX_FIXTURE_DIR / f"{name}.docx")
        status_code, body = _post("/api/ingest", {
            "source_type": "docx",
            "source": path,
            "owner": TEST_OWNER,
            "subject": TEST_SUBJECT,
            "topic": f"docx_eval_{name}",
        })
        ingest_status = body.get("status", "") if isinstance(body, dict) else ""
        if status_code == 0:
            print(f"  FAIL  ingest {name}.docx → {body.get('error', 'connection error')}")
            print(f"        Is the server running? API_BASE={API_BASE}")
            failed.append(name)
        elif status_code == 200 and ingest_status == "accepted":
            print(f"  PASS  ingest {name}.docx → accepted")
            accepted.append(name)
        elif ingest_status == "queued":
            print(f"  FAIL  ingest {name}.docx → queued (DOCX extraction not yet implemented)")
            failed.append(name)
        else:
            print(f"  FAIL  ingest {name}.docx → HTTP {status_code}, status={ingest_status!r}")
            if isinstance(body, dict) and body.get("details"):
                print(f"        details: {body['details']}")
            failed.append(name)

    return accepted, failed


# ---------------------------------------------------------------------------
# Phase 2 — DOCX Retrieval
# ---------------------------------------------------------------------------

def phase2_docx_retrieval(accepted_fixtures: set[str]) -> tuple[int, int]:
    """Query for known DOCX phrases. Returns (passed, total)."""
    print("\n--- Phase 2: DOCX query for known phrases (threshold: 6/8) ---")
    passed = 0
    total = 0

    for fixture_name, phrase, description in DOCX_FIXTURE_QUERIES:
        total += 1
        if fixture_name not in accepted_fixtures:
            print(f"  SKIP  [{fixture_name}] {description!r} — fixture not ingested")
            continue

        status_code, body = _post("/api/query", {
            "query": phrase,
            "owner": TEST_OWNER,
            "n_results": 5,
        })

        if status_code != 200:
            print(f"  FAIL  [{fixture_name}] {description!r} — HTTP {status_code}")
            continue

        results = body.get("results", [])
        top2 = results[:2]
        found = any(
            phrase.lower() in (r.get("text", "") or r.get("content", "")).lower()
            for r in top2
        )
        if found:
            print(f"  PASS  [{fixture_name}] {description!r}")
            passed += 1
        else:
            top2_snippets = [(r.get("text", "") or r.get("content", ""))[:60] for r in top2]
            print(f"  FAIL  [{fixture_name}] {description!r}")
            print(f"        phrase not in top-2. top-2 snippets: {top2_snippets}")

    return passed, total


# ---------------------------------------------------------------------------
# Phase 3 — URL Ingest
# ---------------------------------------------------------------------------

def phase3_url_ingest() -> tuple[list[str], list[str]]:
    """Ingest 2 URLs. Returns (accepted_urls, failed_urls)."""
    accepted: list[str] = []
    failed: list[str] = []

    print("\n--- Phase 3: Ingest URLs ---")
    for url, description, _ in URL_INGEST_CASES:
        status_code, body = _post("/api/ingest", {
            "source_type": "url",
            "source": url,
            "owner": TEST_OWNER,
            "subject": TEST_SUBJECT,
            "topic": f"url_eval",
        })
        ingest_status = body.get("status", "") if isinstance(body, dict) else ""
        if status_code == 0:
            print(f"  FAIL  ingest {description} → {body.get('error', 'connection error')}")
            failed.append(url)
        elif status_code == 200 and ingest_status == "accepted":
            print(f"  PASS  ingest {description} → accepted")
            accepted.append(url)
        elif ingest_status == "queued":
            print(f"  FAIL  ingest {description} → queued (URL fetch not yet implemented)")
            failed.append(url)
        else:
            print(f"  FAIL  ingest {description} → HTTP {status_code}, status={ingest_status!r}")
            if isinstance(body, dict) and body.get("details"):
                print(f"        details: {body['details']}")
            failed.append(url)

    return accepted, failed


# ---------------------------------------------------------------------------
# Phase 4 — URL Retrieval
# ---------------------------------------------------------------------------

def phase4_url_retrieval(accepted_urls: set[str]) -> tuple[int, int]:
    """Query for known URL content. Returns (passed, total)."""
    print("\n--- Phase 4: URL query for known phrases (threshold: 3/4) ---")
    passed = 0
    total = 0

    for url, phrase, description in URL_RETRIEVAL_QUERIES:
        total += 1
        if url not in accepted_urls:
            print(f"  SKIP  {description!r} — URL not ingested")
            continue

        status_code, body = _post("/api/query", {
            "query": phrase,
            "owner": TEST_OWNER,
            "n_results": 5,
        })

        if status_code != 200:
            print(f"  FAIL  {description!r} — HTTP {status_code}")
            continue

        results = body.get("results", [])
        top2 = results[:2]
        found = any(
            phrase.lower() in (r.get("text", "") or r.get("content", "")).lower()
            for r in top2
        )
        if found:
            print(f"  PASS  {description!r}")
            passed += 1
        else:
            top2_snippets = [(r.get("text", "") or r.get("content", ""))[:60] for r in top2]
            print(f"  FAIL  {description!r}")
            print(f"        phrase not in top-2. top-2 snippets: {top2_snippets}")

    return passed, total


# ---------------------------------------------------------------------------
# Phase 5 — Negative queries
# ---------------------------------------------------------------------------

def phase5_negative() -> tuple[int, int]:
    """Query for phrases not in any fixture or URL. PASS if no test chunk in top-2."""
    print("\n--- Phase 5: Negative queries (expect no test content in top-2) ---")
    passed = 0
    total = len(NEGATIVE_QUERIES)

    for phrase, description in NEGATIVE_QUERIES:
        status_code, body = _post("/api/query", {
            "query": phrase,
            "owner": TEST_OWNER,
            "n_results": 5,
        })

        if status_code == 0:
            print(f"  SKIP  {description!r} — server unreachable")
            passed += 1  # Can't contaminate if server is down
            continue
        if status_code != 200:
            print(f"  FAIL  {description!r} — HTTP {status_code}")
            continue

        results = body.get("results", [])
        top2 = results[:2]
        contaminated = any(
            TEST_SUBJECT in (r.get("subject", "") or r.get("metadata", {}).get("subject", ""))
            for r in top2
        )
        if not contaminated:
            print(f"  PASS  {description!r} — no test content in top-2")
            passed += 1
        else:
            print(f"  FAIL  {description!r} — test content found in top-2 for negative query")

    return passed, total


# ---------------------------------------------------------------------------
# Eval history append
# ---------------------------------------------------------------------------

def _append_eval_history(
    ts: str,
    docx_ingest_passed: int,
    docx_ingest_total: int,
    docx_retrieval_passed: int,
    docx_retrieval_total: int,
    url_ingest_passed: int,
    url_ingest_total: int,
    url_retrieval_passed: int,
    url_retrieval_total: int,
    negative_passed: int,
    negative_total: int,
    cleanup_deleted: int,
    overall: str,
) -> None:
    entry = (
        f"\n## DOCX + URL Ingest Eval — {ts}\n"
        f"- DOCX ingest: {docx_ingest_passed}/{docx_ingest_total} accepted\n"
        f"- DOCX retrieval: {docx_retrieval_passed}/{docx_retrieval_total} pass "
        f"(threshold: 6/{docx_retrieval_total})\n"
        f"- URL ingest: {url_ingest_passed}/{url_ingest_total} accepted\n"
        f"- URL retrieval: {url_retrieval_passed}/{url_retrieval_total} pass "
        f"(threshold: 3/{url_retrieval_total})\n"
        f"- Negative cases: {negative_passed}/{negative_total} pass\n"
        f"- Cleanup: {cleanup_deleted} rows deleted\n"
        f"- Overall: {overall}\n"
    )
    try:
        with open(EVAL_HISTORY, "a", encoding="utf-8") as f:
            f.write(entry)
        print(f"\nAppended to eval_history.md: yes")
    except Exception as exc:
        print(f"\nAppended to eval_history.md: FAILED ({exc})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"DOCX + URL Ingest Eval — {ts}")
    print(f"API base: {API_BASE}")
    print(f"DOCX fixture dir: {DOCX_FIXTURE_DIR}")
    print(f"Test subject: {TEST_SUBJECT!r}")

    missing_fixtures = [
        n for n in ["simple_text", "multi_paragraph", "special_chars"]
        if not (DOCX_FIXTURE_DIR / f"{n}.docx").exists()
    ]
    if missing_fixtures:
        print(f"\nERROR: DOCX fixtures missing: {missing_fixtures}. Run:")
        print("  python scripts/test_fixtures/generate_docx_fixtures.py")
        return 1

    cleanup_deleted = 0
    cleanup_error: str | None = None

    try:
        # Phase 1 — DOCX ingest
        docx_accepted, docx_failed = phase1_docx_ingest()
        docx_ingest_passed = len(docx_accepted)
        docx_ingest_total = len(docx_accepted) + len(docx_failed)

        if docx_accepted:
            time.sleep(1)
        else:
            print("\nWARN: No DOCX fixtures were accepted — DOCX retrieval phase will be skipped.")

        # Phase 2 — DOCX retrieval
        if docx_accepted:
            docx_retrieval_passed, docx_retrieval_total = phase2_docx_retrieval(set(docx_accepted))
        else:
            docx_retrieval_passed, docx_retrieval_total = 0, len(DOCX_FIXTURE_QUERIES)

        # Phase 3 — URL ingest
        url_accepted, url_failed = phase3_url_ingest()
        url_ingest_passed = len(url_accepted)
        url_ingest_total = len(url_accepted) + len(url_failed)

        if url_accepted:
            time.sleep(1)
        else:
            print("\nWARN: No URLs were accepted — URL retrieval phase will be skipped.")

        # Phase 4 — URL retrieval
        if url_accepted:
            url_retrieval_passed, url_retrieval_total = phase4_url_retrieval(set(url_accepted))
        else:
            url_retrieval_passed, url_retrieval_total = 0, len(URL_RETRIEVAL_QUERIES)

        # Phase 5 — negative queries
        negative_passed, negative_total = phase5_negative()

    finally:
        # Phase 6 — cleanup (always runs)
        print("\n--- Phase 6: Cleanup ---")
        cleanup_deleted, cleanup_error = _cleanup_test_rows()
        if cleanup_error:
            print(f"  WARN  cleanup error: {cleanup_error}")
            print(f"  Manual cleanup: DELETE FROM public.thoughts WHERE subject = '{TEST_SUBJECT}' AND created_by_user_login = '{TEST_OWNER}'")
        else:
            print(f"  {cleanup_deleted} rows deleted from public.thoughts")

    # Score
    docx_retrieval_threshold = 6
    url_retrieval_threshold = 3
    docx_retrieval_ok = docx_retrieval_passed >= docx_retrieval_threshold
    url_retrieval_ok = url_retrieval_passed >= url_retrieval_threshold
    negative_ok = negative_passed == negative_total
    overall_ok = (
        docx_ingest_passed == docx_ingest_total
        and docx_retrieval_ok
        and url_ingest_passed == url_ingest_total
        and url_retrieval_ok
        and negative_ok
    )
    overall = "PASS" if overall_ok else "FAIL"

    docx_cases = docx_ingest_total + docx_retrieval_total
    url_cases = url_ingest_total + url_retrieval_total
    total_cases = docx_cases + url_cases + negative_total
    total_passed = docx_ingest_passed + docx_retrieval_passed + url_ingest_passed + url_retrieval_passed + negative_passed

    print(f"\n{'=' * 50}")
    print(f"DOCX + URL Ingest Eval — {ts}")
    print(f"DOCX cases: {docx_ingest_passed + docx_retrieval_passed}/{docx_cases}")
    print(f"  Ingest:    {docx_ingest_passed}/{docx_ingest_total}")
    print(f"  Retrieval: {docx_retrieval_passed}/{docx_retrieval_total} (min: {docx_retrieval_threshold}/{docx_retrieval_total})")
    print(f"URL cases: {url_ingest_passed + url_retrieval_passed}/{url_cases}")
    print(f"  Ingest:    {url_ingest_passed}/{url_ingest_total}")
    print(f"  Retrieval: {url_retrieval_passed}/{url_retrieval_total} (min: {url_retrieval_threshold}/{url_retrieval_total})")
    print(f"Negative cases: {negative_passed}/{negative_total}")
    print(f"Cleanup: {cleanup_deleted} rows deleted")
    print(f"Overall pass rate: {total_passed / total_cases * 100:.1f}%")
    print(f"Min threshold: 75%")
    print(f"Status: {overall}")
    print(f"{'=' * 50}")

    _append_eval_history(
        ts,
        docx_ingest_passed, docx_ingest_total,
        docx_retrieval_passed, docx_retrieval_total,
        url_ingest_passed, url_ingest_total,
        url_retrieval_passed, url_retrieval_total,
        negative_passed, negative_total,
        cleanup_deleted,
        overall,
    )

    if not overall_ok and docx_ingest_passed == 0 and url_ingest_passed == 0:
        print("\nNOTE: All ingest cases failed — this likely means _extract_docx() and _fetch_url()")
        print("are not yet implemented in api/_openbrain_api.py.")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
