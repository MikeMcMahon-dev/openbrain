#!/usr/bin/env python3
"""ADR-018 P5 — query the vault as-of a point in valid-time.

    python scripts/as_of.py 2026-07-01                     # everything true on that date
    python scripts/as_of.py 2026-07-01 --system SpectreNet
    python scripts/as_of.py 2026-07-01 --component dns-current-state

Shows rows whose real lifespan (valid_from..valid_until) contained the given instant, including
rows since superseded — the preserved history, retrievable. Read-only.
"""
from __future__ import annotations

import argparse
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

sys.path.insert(0, str(ROOT))

from api.temporal_query import as_of  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("when", help="ISO date/timestamp, e.g. 2026-07-01")
    ap.add_argument("--system")
    ap.add_argument("--component")
    ap.add_argument("--domain")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()

    rows = as_of(args.when, system=args.system, component_key=args.component,
                 domain=args.domain, limit=args.limit)
    print(f"{len(rows)} row(s) valid as-of {args.when}:\n")
    for r in rows:
        tag = "" if r["status"] == "current" else f" [{r['status']} now]"
        span = f"{r['valid_from']} .. {r['valid_until'] or 'open'}"
        print(f"  {r['id']}  [{r['system'] or '-'}]{tag}")
        print(f"    {span}")
        print(f"    {r['head']!r}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
