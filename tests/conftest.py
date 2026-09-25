"""Pytest bootstrap for the test suite.

Put the repo root on sys.path so `from api import ...` resolves however pytest is
invoked: `make test`, plain `pytest` from the repo root (pytest.ini limits collection
to tests/), or `cd tests && python -m pytest`. The root used to hold a `vault/` symlink
into an iCloud store unreadable for some users, and pytest crashed listing it. It was
removed 2026-09-25.

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
