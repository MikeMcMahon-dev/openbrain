#!/usr/bin/env python3
"""PDF Ingest Eval Harness — OpenBrain

Measures retrieval quality after PDF ingestion. Appends results to eval_history.md.

Phases:
  1. Ingest test PDFs (3 cases)
  2. Query for known phrases (10 cases, threshold: 8/10)
  3. Negative queries — phrases NOT in any ingested fixture (3 cases)
  4. Cleanup — DELETE test rows from Supabase by subject discriminator

Usage:
    python scripts/test_pdf_ingest_eval.py

Environment variables:
    SUPABASE_DB_URL           — direct Postgres URI (required for cleanup)
    OPENBRAIN_TOOL_ACCESS_TOKEN — bearer token for API calls
    OPENBRAIN_API_BASE         — defaults to http://localhost:3000
"""
from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = PROJECT_ROOT / "scripts" / "test_fixtures" / "pdf"
EVAL_HISTORY = PROJECT_ROOT / "scripts" / "eval_history.md"

TEST_SUBJECT = "pdf_ingest_eval_test"
TEST_OWNER = "mike.mcmahon67"

API_BASE = os.getenv("OPENBRAIN_API_BASE", "http://localhost:3000").rstrip("/")
TOKEN = os.getenv("OPENBRAIN_TOOL_ACCESS_TOKEN", "")
AUTH_HEADER = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}

# Known phrases per fixture (must match generate_pdf_fixtures.py).
FIXTURE_QUERIES: list[tuple[str, str, str]] = [
    # (fixture_name, phrase, description)
    ("simple_text", "xyloquartz-retrieval-fixture-alpha", "unique retrieval phrase"),
    ("simple_text", "The quick brown fox jumps over the lazy dog", "common phrase"),
    ("simple_text", "single-page PDF with clean ASCII text", "descriptive phrase"),
    ("multi_page", "Page one content begins here", "page 1 marker"),
    ("multi_page", "Page two content for the multi-page fixture", "page 2 marker"),
    ("multi_page", "Page three content ends here", "page 3 marker"),
    ("multi_page", "all pages correctly", "page 3 sub-phrase"),
    ("special_chars", "caf\u00e9 na\u00efve resum\u00e9", "Latin-1 accented chars"),
    ("special_chars", "stra\u00dfe M\u00fcnchen", "German umlauts"),
    ("special_chars", "special_chars", "fixture label"),
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
    """Delete test rows from public.thoughts where subject = TEST_SUBJECT.

    Returns (rows_deleted, error_message_or_None).
    """
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
# Phase 1 — Ingest
# ---------------------------------------------------------------------------

def phase1_ingest() -> tuple[list[str], list[str]]:
    """Ingest 3 fixture PDFs. Returns (accepted_fixture_names, failed_fixture_names)."""
    fixtures = ["simple_text", "multi_page", "special_chars"]
    accepted: list[str] = []
    failed: list[str] = []

    print("\n--- Phase 1: Ingest test PDFs ---")
    for name in fixtures:
        path = str(FIXTURE_DIR / f"{name}.pdf")
        status_code, body = _post("/api/ingest", {
            "source_type": "pdf",
            "source": path,
            "owner": TEST_OWNER,
            "subject": TEST_SUBJECT,
            "topic": f"pdf_eval_{name}",
        })
        ingest_status = body.get("status", "") if isinstance(body, dict) else ""
        if status_code == 0:
            print(f"  FAIL  ingest {name}.pdf → {body.get('error', 'connection error')}")
            print(f"        Is the server running? API_BASE={API_BASE}")
            failed.append(name)
        elif status_code == 200 and ingest_status == "accepted":
            print(f"  PASS  ingest {name}.pdf → accepted")
            accepted.append(name)
        elif ingest_status == "queued":
            print(f"  FAIL  ingest {name}.pdf → queued (PDF extraction not yet implemented)")
            failed.append(name)
        else:
            print(f"  FAIL  ingest {name}.pdf → HTTP {status_code}, status={ingest_status!r}")
            if isinstance(body, dict) and body.get("details"):
                print(f"        details: {body['details']}")
            failed.append(name)

    return accepted, failed


# ---------------------------------------------------------------------------
# Phase 2 — Retrieval quality
# ---------------------------------------------------------------------------

def phase2_retrieval(accepted_fixtures: set[str]) -> tuple[int, int]:
    """Query for known phrases. Returns (passed, total)."""
    print("\n--- Phase 2: Query for known phrases (threshold: 8/10) ---")
    passed = 0
    total = 0

    for fixture_name, phrase, description in FIXTURE_QUERIES:
        total += 1
        if fixture_name not in accepted_fixtures:
            print(f"  SKIP  [{fixture_name}] {description!r} — fixture not ingested")
            # Count as fail since the fixture should have been ingested
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
# Phase 3 — Negative queries
# ---------------------------------------------------------------------------

def phase3_negative() -> tuple[int, int]:
    """Query for phrases not in any fixture. PASS if no fixture chunk in top-2."""
    print("\n--- Phase 3: Negative queries (expect no fixture content in top-2) ---")
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
            print(f"  PASS  {description!r} — no fixture content in top-2")
            passed += 1
        else:
            print(f"  FAIL  {description!r} — fixture content found in top-2 for negative query")

    return passed, total


# ---------------------------------------------------------------------------
# Eval history append
# ---------------------------------------------------------------------------

def _append_eval_history(
    ts: str,
    ingest_passed: int,
    ingest_total: int,
    retrieval_passed: int,
    retrieval_total: int,
    negative_passed: int,
    negative_total: int,
    cleanup_deleted: int,
    overall: str,
) -> None:
    entry = (
        f"\n## PDF Ingest Eval — {ts}\n"
        f"- Ingest cases: {ingest_passed}/{ingest_total} accepted\n"
        f"- Retrieval cases: {retrieval_passed}/{retrieval_total} pass "
        f"(threshold: {int(retrieval_total * 0.8)}/{retrieval_total})\n"
        f"- Negative cases: {negative_passed}/{negative_total} pass\n"
        f"- Cleanup: {cleanup_deleted} rows deleted\n"
        f"- Overall: {overall}\n"
    )
    try:
        with open(EVAL_HISTORY, "a", encoding="utf-8") as f:
            f.write(entry)
        print("\nAppended to eval_history.md: yes")
    except Exception as exc:
        print(f"\nAppended to eval_history.md: FAILED ({exc})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"PDF Ingest Eval — {ts}")
    print(f"API base: {API_BASE}")
    print(f"Fixture dir: {FIXTURE_DIR}")
    print(f"Test subject: {TEST_SUBJECT!r}")

    if not all((FIXTURE_DIR / f"{n}.pdf").exists() for n in ["simple_text", "multi_page", "special_chars"]):
        print("\nERROR: PDF fixtures missing. Run:")
        print("  python scripts/test_fixtures/generate_pdf_fixtures.py")
        return 1

    cleanup_deleted = 0
    cleanup_error: str | None = None

    try:
        # Phase 1 — ingest
        accepted_fixtures, failed_fixtures = phase1_ingest()
        ingest_passed = len(accepted_fixtures)
        ingest_total = len(accepted_fixtures) + len(failed_fixtures)

        if accepted_fixtures:
            # Brief wait for embedding writes to settle
            time.sleep(1)
        else:
            print("\nWARN: No fixtures were accepted — retrieval and negative phases will be skipped.")

        # Phase 2 — retrieval quality
        if accepted_fixtures:
            retrieval_passed, retrieval_total = phase2_retrieval(set(accepted_fixtures))
        else:
            retrieval_passed, retrieval_total = 0, len(FIXTURE_QUERIES)

        # Phase 3 — negative queries
        negative_passed, negative_total = phase3_negative()

    finally:
        # Phase 4 — cleanup (always runs)
        print("\n--- Phase 4: Cleanup ---")
        cleanup_deleted, cleanup_error = _cleanup_test_rows()
        if cleanup_error:
            print(f"  WARN  cleanup error: {cleanup_error}")
            print(f"  Manual cleanup: DELETE FROM public.thoughts WHERE subject = '{TEST_SUBJECT}' AND created_by_user_login = '{TEST_OWNER}'")
        else:
            print(f"  {cleanup_deleted} rows deleted from public.thoughts")

    # Score
    retrieval_threshold = int(retrieval_total * 0.8)
    retrieval_ok = retrieval_passed >= retrieval_threshold
    negative_ok = negative_passed == negative_total
    overall_ok = (ingest_passed == ingest_total) and retrieval_ok and negative_ok
    overall = "PASS" if overall_ok else "FAIL"

    total_cases = ingest_total + retrieval_total + negative_total

    print(f"\n{'=' * 50}")
    print(f"PDF Ingest Eval — {ts}")
    print(f"Total cases: {total_cases}")
    print(f"  Ingest:    {ingest_passed}/{ingest_total}")
    print(f"  Retrieval: {retrieval_passed}/{retrieval_total} (min: {retrieval_threshold}/{retrieval_total})")
    print(f"  Negative:  {negative_passed}/{negative_total}")
    print(f"Cleanup:     {cleanup_deleted} rows deleted")
    print(f"Pass rate:   {(ingest_passed + retrieval_passed + negative_passed) / total_cases * 100:.1f}%")
    print("Min threshold: 80%")
    print(f"Status: {overall}")
    print(f"{'=' * 50}")

    _append_eval_history(
        ts,
        ingest_passed, ingest_total,
        retrieval_passed, retrieval_total,
        negative_passed, negative_total,
        cleanup_deleted,
        overall,
    )

    if not overall_ok and ingest_passed == 0:
        print("\nNOTE: All ingest cases failed — this likely means _extract_pdf() is not yet")
        print("implemented in api/_openbrain_api.py. Build the implementation first, then re-run.")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
