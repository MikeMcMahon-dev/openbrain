#!/usr/bin/env python3
"""
OpenBrain Error Log Viewer
===========================
Query the error_log table for recent API errors.

Usage:
    python scripts/error_log.py              # last 24 hours
    python scripts/error_log.py --hours 48   # last 48 hours
    python scripts/error_log.py --tail 10    # last 10 errors
    python scripts/error_log.py --full       # include full tracebacks
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env.local")

import psycopg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=24, help="Look back N hours (default 24)")
    parser.add_argument("--tail",  type=int, default=None, help="Show last N errors regardless of time")
    parser.add_argument("--full",  action="store_true", help="Include full tracebacks")
    args = parser.parse_args()

    db_url = os.getenv("SUPABASE_DB_URL") or os.getenv("DATABASE_URL")
    with psycopg.connect(db_url, connect_timeout=10) as conn:
        if args.tail:
            rows = conn.execute("""
                SELECT id, created_at, path, method, exc_type, exc_message, traceback, request_id
                FROM public.error_log
                ORDER BY created_at DESC
                LIMIT %s
            """, [args.tail]).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, created_at, path, method, exc_type, exc_message, traceback, request_id
                FROM public.error_log
                WHERE created_at >= NOW() - (%s || ' hours')::INTERVAL
                ORDER BY created_at DESC
            """, [str(args.hours)]).fetchall()

    if not rows:
        print(f"No errors in the last {args.hours} hours." if not args.tail else "No errors found.")
        return

    print(f"\n{'='*60}")
    print(f"  OpenBrain Error Log — {len(rows)} error(s)")
    print(f"{'='*60}\n")

    for row in rows:
        id_, created_at, path, method, exc_type, exc_message, traceback, request_id = row
        print(f"[{id_}] {created_at.strftime('%Y-%m-%d %H:%M:%S UTC') if created_at else '?'}")
        print(f"  {method or '?'} {path or '?'}")
        print(f"  {exc_type}: {exc_message}")
        if request_id:
            print(f"  request_id: {request_id}")
        if args.full and traceback:
            print(f"\n  Traceback:\n")
            for line in traceback.strip().splitlines():
                print(f"    {line}")
        print()


if __name__ == "__main__":
    main()
