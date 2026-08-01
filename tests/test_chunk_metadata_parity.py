"""Two-table metadata parity guard (ADR-018a item 10a / P2 step 0).

`knowledge_chunked` currently DUPLICATES `knowledge`'s mutable metadata (system, status,
component_key, tags, domain, environment), synced by hand in `_mirror_chunks()`. ADR-018a
Decision item 3 removes those copies — chunks will JOIN to `knowledge` on `document_id` — so
drift becomes structurally impossible. Until migration 007 drops the columns they can and do
drift: three instances in nine days, most recently the 2026-08-01 re-key, which touched
`knowledge` only and left the re-keyed docs' chunks carrying a stale `system`.

This test asserts the two tables agree on every mirrored metadata column for the same
`document_id`. It is EXPECTED TO FAIL today — that RED is the proof it detects the drift
(ADR-018a: "a parity test that passes today is not testing anything"). It is marked
`xfail(strict=True)`: the suite stays green now, and when migration 007 drops the columns the
divergence becomes 0, this xPASSes, strict-xfail fails the build, and this file is deleted
along with the columns it guards.

Read-only. Requires `.env.local` with `SUPABASE_DB_URL`; skipped without it.
Run: `.venv/bin/python -m pytest tests/test_chunk_metadata_parity.py -v`
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_ENV = ROOT / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

import psycopg  # noqa: E402

# Every metadata column knowledge_chunked mirrors from knowledge today. Migration 007 drops all
# of these from the chunked table (ADR-018a Decision item 3). `created_by` stays denormalised
# (immutable, security-critical owner filter) — no divergence risk, so it is NOT checked here.
_MIRRORED = ["system", "status", "component_key", "tags", "domain", "environment"]


def _dsn() -> str | None:
    return (
        os.getenv("SUPABASE_DB_URL")
        or os.getenv("OPENBRAIN_SUPABASE_CONNECTION_STRING")
        or os.getenv("DATABASE_URL")
    )


needs_db = pytest.mark.skipif(not _dsn(), reason="no SUPABASE_DB_URL in env")


def _divergence_by_column() -> dict[str, int]:
    """Per-column count of chunks whose value disagrees with their parent knowledge row.

    IS DISTINCT FROM so a NULL on one side and a value on the other counts as divergence
    (the exact shape of the re-key drift: parent system set, chunk system still NULL).
    """
    checks = ", ".join(
        f"count(*) FILTER (WHERE k.{c} IS DISTINCT FROM kc.{c}) AS {c}" for c in _MIRRORED
    )
    sql = (
        f"SELECT {checks} FROM public.knowledge_chunked kc "
        "JOIN public.knowledge k ON k.id = kc.document_id"
    )
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
    return dict(zip(_MIRRORED, row))


@needs_db
@pytest.mark.xfail(
    strict=True,
    reason="knowledge_chunked still mirrors metadata; drift exists until migration 007 drops "
    "the columns (ADR-018a item 3). When it lands this xPASSes -> delete this file.",
)
def test_chunk_metadata_matches_parent():
    div = _divergence_by_column()
    total = sum(div.values())
    assert total == 0, (
        "knowledge_chunked diverges from knowledge on mirrored metadata "
        f"(per-column chunk-row counts): {div}"
    )
