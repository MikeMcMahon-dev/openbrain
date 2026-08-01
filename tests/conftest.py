"""Pytest bootstrap for the test suite.

Put the repo root on sys.path so `from api import ...` resolves when pytest is
invoked from inside tests/ (which is how the suite must be run — the repo root
holds a `vault/` symlink into an iCloud store that is unreadable for some users,
so pytest's rootdir scandir crashes if it starts there). Run via `make test`,
or `cd tests && python -m pytest`.

Also load .env.local into os.environ HERE (conftest runs before any test module or
`api` import), so DB-dependent tests see SUPABASE_DB_URL regardless of collection
order. Previously each test file loaded it at its own top level, which made the
DB tests depend on alphabetical file order — fragile, and it broke when an early
loader was deleted. setdefault: never clobber an already-set env var.
"""
import os
import sys
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

_ENV = Path(_REPO_ROOT) / ".env.local"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k, _v.strip().strip('"').strip("'"))
