#!/usr/bin/env python3
"""Trial a prod SQL block in a rolled-back transaction — the MANDATORY pre-handoff check
(ADR-018a item 10e).

The rule this enforces: **no prod SQL is handed to Mike without this tool's output attached.**
It runs the given statements inside `BEGIN … ROLLBACK`, so it reproduces every dependency,
constraint, and permission error against the REAL prod schema and data, then rolls back and
changes nothing. If it prints TRIAL PASSED, the block is safe to run for real. If it prints
TRIAL FAILED, the printed error is the exact one the operator would hit — fix it before handoff.

This exists because writing the rule into an ADR did not make it happen; a one-command,
zero-friction tool + a visible artifact (its output) is what makes skipping the check obvious.

Always rolls back — never mutates. Usage:
    python scripts/sql_trial.py "UPDATE ...; ALTER TABLE ...;"
    python scripts/sql_trial.py < block.sql
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_ENV = ROOT / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))

import psycopg  # noqa: E402


def main() -> int:
    sql = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    if not sql.strip():
        print("usage: sql_trial.py \"<sql>\"   (or pipe SQL on stdin)")
        return 2
    dsn = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        print("no SUPABASE_DB_URL in env (.env.local)")
        return 2

    conn = psycopg.connect(dsn)
    conn.autocommit = False  # explicit transaction so ROLLBACK is guaranteed
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            status = cur.statusmessage
        conn.rollback()
        print("TRIAL PASSED — executed in a transaction, then ROLLED BACK (prod unchanged).")
        if status:
            print(f"  last statement: {status}")
        print("  -> safe to hand over, WITH this output attached as evidence.")
        return 0
    except Exception as exc:
        conn.rollback()
        print("TRIAL FAILED (rolled back — prod unchanged):")
        print(f"  {type(exc).__name__}: {exc}")
        print("  -> this is the exact error the operator would hit. Fix it BEFORE handoff.")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
