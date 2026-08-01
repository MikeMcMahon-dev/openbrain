#!/usr/bin/env python3
"""ADR-018 P4 — review contradiction candidates from the CLI.

    python scripts/contradiction_review.py detect            # scan for new candidate pairs
    python scripts/contradiction_review.py list              # show the pending review queue
    python scripts/contradiction_review.py confirm <pair_id> <loser_id> [note]
    python scripts/contradiction_review.py dismiss <pair_id> [note]

`confirm` retires the loser via a contradiction_confirmed supersession event (P3 rails).
`dismiss` writes nothing and the pair is never re-flagged. Read paths are read-only.
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

sys.path.insert(0, str(ROOT))

from api.contradiction_detect import confirm, detect_candidates, dismiss, list_pending  # noqa: E402

_ACTOR = os.getenv("USER") or "mmcmahon"


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd = argv[0]

    if cmd == "detect":
        rep = detect_candidates()
        print(f"detect (threshold={rep['threshold']}): {rep['inserted']} new, "
              f"{rep['pending']} pending total.")
        return 0

    if cmd == "list":
        rows = list_pending()
        if not rows:
            print("No pending contradiction candidates.")
            return 0
        print(f"{len(rows)} pending candidate pair(s):\n")
        for r in rows:
            print(f"  pair {r['pair_id']}  [{r['system']}]  sim={r['similarity']}")
            print(f"    lo {r['id_lo']}: {r['lo_head']!r}")
            print(f"    hi {r['id_hi']}: {r['hi_head']!r}")
            print("    confirm: contradiction_review.py confirm "
                  f"{r['pair_id']} <lo|hi id>   dismiss: ...dismiss {r['pair_id']}\n")
        return 0

    if cmd == "confirm":
        if len(argv) < 3:
            print("usage: confirm <pair_id> <loser_id> [note]")
            return 2
        note = " ".join(argv[3:]) or None
        res = confirm(argv[1], argv[2], _ACTOR, note)
        print(res)
        return 0 if res.get("ok") else 1

    if cmd == "dismiss":
        if len(argv) < 2:
            print("usage: dismiss <pair_id> [note]")
            return 2
        note = " ".join(argv[2:]) or None
        res = dismiss(argv[1], _ACTOR, note)
        print(res)
        return 0 if res.get("ok") else 1

    print(f"unknown command: {cmd}")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
