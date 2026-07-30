"""Pytest bootstrap for the test suite.

Put the repo root on sys.path so `from api import ...` resolves when pytest is
invoked from inside tests/ (which is how the suite must be run — the repo root
holds a `vault/` symlink into an iCloud store that is unreadable for some users,
so pytest's rootdir scandir crashes if it starts there). Run via `make test`,
or `cd tests && python -m pytest`.
"""
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
