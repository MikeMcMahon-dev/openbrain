from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent
if str(API_DIR) not in sys.path:
    sys.path.insert(0, str(API_DIR))

from _openbrain_api import response_payload


def handler(request):
    return response_payload(200, {"status": "openbrain vercel api online"})
