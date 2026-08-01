#!/usr/bin/env python3
"""ADR-018 P3 — run the supersession reconciliation from the CLI.

Prints the drift report and exits 0 (clean) or 1 (drift found), so it fits `make check` and a
pre-push hook. Read-only; never mutates. Recovery from any drift is REPLAY from
supersession_events, not restore — this only reports.

    python scripts/reconcile_supersession.py
"""
from __future__ import annotations

import json
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

from api.supersession_reconcile import reconcile_supersession  # noqa: E402


def main() -> int:
    report = reconcile_supersession()
    print(f"supersession reconcile: {report['events']} events, "
          f"{report['superseded']} superseded rows, drift={report['drift_count']}")
    if report["ok"]:
        print("CLEAN — stored status matches the event log.")
        return 0
    print("DRIFT — stored status disagrees with the event log (replay to recover):")
    print(json.dumps(report["drift"], indent=2))
    return 1


if __name__ == "__main__":
    sys.exit(main())
