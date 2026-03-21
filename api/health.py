from __future__ import annotations

from api._openbrain_api import response_payload


def handler(request):
    return response_payload(200, {"status": "openbrain vercel api online"})
