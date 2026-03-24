#!/usr/bin/env python3
"""Brain activity report — entries by owner for a given time window.

Usage:
    .venv/bin/python scripts/brain_report.py                     # last 7 days
    .venv/bin/python scripts/brain_report.py --days 30           # last 30 days
    .venv/bin/python scripts/brain_report.py --synopsis          # + recent entry previews
    .venv/bin/python scripts/brain_report.py --daily             # + day-by-day breakdown
    .venv/bin/python scripts/brain_report.py --daily --synopsis  # all of the above
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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

SYNOPSIS_ENTRIES = 3
SYNOPSIS_WIDTH   = 120


def run_report(days: int, synopsis: bool, daily: bool) -> None:
    conn = connect(_db_conninfo(), row_factory=dict_row)
    try:
        summary_rows = conn.execute(
            """
            SELECT
                COALESCE(created_by_user_login, slack_username, 'unknown') AS owner,
                COUNT(*)        AS entries,
                MIN(created_at) AS earliest,
                MAX(created_at) AS latest
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

        daily_rows = None
        if daily:
            daily_rows = conn.execute(
                """
                SELECT
                    COALESCE(created_by_user_login, slack_username, 'unknown') AS owner,
                    created_at::date AS day,
                    COUNT(*)         AS entries
                FROM public.thoughts
                WHERE created_at >= NOW() - (%s || ' days')::INTERVAL
                GROUP BY 1, 2
                ORDER BY 1, 2 DESC
                """,
                [str(days)],
            ).fetchall()

        synopsis_rows = None
        if synopsis:
            synopsis_rows = conn.execute(
                """
                SELECT DISTINCT ON (owner)
                    COALESCE(created_by_user_login, slack_username, 'unknown') AS owner,
                    created_at,
                    content,
                    source_type
                FROM public.thoughts
                WHERE created_at >= NOW() - (%s || ' days')::INTERVAL
                ORDER BY owner, created_at DESC
                """,
                [str(days)],
            ).fetchall()

            # Fetch up to SYNOPSIS_ENTRIES recent entries per owner
            owners = [r["owner"] for r in summary_rows]
            synopsis_detail: dict[str, list] = {o: [] for o in owners}
            for owner in owners:
                detail = conn.execute(
                    """
                    SELECT created_at, content, source_type
                    FROM public.thoughts
                    WHERE COALESCE(created_by_user_login, slack_username, 'unknown') = %s
                      AND created_at >= NOW() - (%s || ' days')::INTERVAL
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    [owner, str(days), SYNOPSIS_ENTRIES],
                ).fetchall()
                synopsis_detail[owner] = detail

    finally:
        conn.close()

    # ── Summary ──────────────────────────────────────────────────────────────
    print(f"\nOpenBrain Activity Report — last {days} day{'s' if days != 1 else ''}")
    print("=" * 70)

    if not summary_rows:
        print("  No entries found in this time window.")
        print()
        return

    print(f"  {'Owner':<25} {'Entries':>7}  {'First':^19}  {'Last':^19}")
    print(f"  {'-'*25} {'-'*7}  {'-'*19}  {'-'*19}")
    for r in summary_rows:
        earliest = str(r["earliest"])[:19] if r["earliest"] else "—"
        latest   = str(r["latest"])[:19]   if r["latest"]   else "—"
        print(f"  {r['owner']:<25} {r['entries']:>7}  {earliest:^19}  {latest:^19}")

    print(f"\n  Total: {total['total']} entries across {len(summary_rows)} user(s)")

    # ── Daily breakdown ───────────────────────────────────────────────────────
    if daily and daily_rows:
        print(f"\n  Day-by-Day Breakdown")
        print(f"  {'-'*50}")
        current_owner = None
        for r in daily_rows:
            if r["owner"] != current_owner:
                current_owner = r["owner"]
                print(f"\n  {current_owner}")
            print(f"    {str(r['day'])}  —  {r['entries']} entr{'y' if r['entries'] == 1 else 'ies'}")

    # ── Synopsis ─────────────────────────────────────────────────────────────
    if synopsis:
        print(f"\n  Recent Entry Preview (last {SYNOPSIS_ENTRIES} per user)")
        print(f"  {'-'*50}")
        for owner in [r["owner"] for r in summary_rows]:
            print(f"\n  {owner}")
            for entry in synopsis_detail.get(owner, []):
                ts      = str(entry["created_at"])[:19]
                preview = (entry["content"] or "").replace("\n", " ").strip()
                if len(preview) > SYNOPSIS_WIDTH:
                    preview = preview[:SYNOPSIS_WIDTH] + "…"
                print(f"    [{ts}] {preview}")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenBrain activity report by user.")
    parser.add_argument("--days",     type=int, default=7, help="Days to look back (default: 7)")
    parser.add_argument("--synopsis", action="store_true",  help="Show recent entry previews per user")
    parser.add_argument("--daily",    action="store_true",  help="Show day-by-day entry breakdown")
    args = parser.parse_args()

    if args.days < 1:
        print("ERROR: --days must be at least 1")
        raise SystemExit(1)

    run_report(args.days, args.synopsis, args.daily)


if __name__ == "__main__":
    main()
