#!/usr/bin/env python3
"""Brain activity report — entries by owner for a given time window.

Usage:
    .venv/bin/python scripts/brain_report.py           # last 7 days (default)
    .venv/bin/python scripts/brain_report.py --days 30 # last 30 days
    .venv/bin/python scripts/brain_report.py --days 1  # today
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Load .env.local so the script works the same way as the rest of the toolchain.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api._openbrain_api import _db_conninfo

try:
    from psycopg import connect
    from psycopg.rows import dict_row
except ImportError:
    print("ERROR: psycopg is not installed. Run: pip install psycopg[binary]")
    raise SystemExit(1)


def run_report(days: int) -> None:
    conn = connect(_db_conninfo(), row_factory=dict_row)
    try:
        rows = conn.execute(
            """
            SELECT
                COALESCE(created_by_user_login, slack_username, 'unknown') AS owner,
                COUNT(*)                                                    AS entries,
                MIN(created_at)                                             AS earliest,
                MAX(created_at)                                             AS latest
            FROM public.thoughts
            WHERE created_at >= NOW() - (%s || ' days')::INTERVAL
            GROUP BY 1
            ORDER BY 2 DESC
            """,
            [str(days)],
        ).fetchall()

        total = conn.execute(
            """
            SELECT COUNT(*) AS total FROM public.thoughts
            WHERE created_at >= NOW() - (%s || ' days')::INTERVAL
            """,
            [str(days)],
        ).fetchone()
    finally:
        conn.close()

    print(f"\nOpenBrain Activity Report — last {days} day{'s' if days != 1 else ''}")
    print("=" * 70)

    if not rows:
        print("  No entries found in this time window.")
        print()
        return

    print(f"  {'Owner':<25} {'Entries':>7}  {'First':^19}  {'Last':^19}")
    print(f"  {'-'*25} {'-'*7}  {'-'*19}  {'-'*19}")
    for r in rows:
        earliest = str(r["earliest"])[:19] if r["earliest"] else "—"
        latest   = str(r["latest"])[:19]   if r["latest"]   else "—"
        print(f"  {r['owner']:<25} {r['entries']:>7}  {earliest:^19}  {latest:^19}")

    print(f"\n  Total: {total['total']} entries across {len(rows)} user(s)")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenBrain activity report by user.")
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to look back (default: 7)",
    )
    args = parser.parse_args()

    if args.days < 1:
        print("ERROR: --days must be at least 1")
        raise SystemExit(1)

    run_report(args.days)


if __name__ == "__main__":
    main()
