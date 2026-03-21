from __future__ import annotations

from api._openbrain_api import response_payload, _resolve_ipv4, _db_conninfo, _db_connect, DB_URL


def handler(request):
    import urllib.parse
    parsed = urllib.parse.urlparse(DB_URL or "")
    hostname = parsed.hostname or ""
    ipv4 = _resolve_ipv4(hostname)
    conninfo_preview = _db_conninfo().replace(parsed.password or "", "***") if DB_URL else "no DB_URL"
    conn, err = _db_connect()
    if conn:
        try:
            conn.close()
        except Exception:
            pass
    return response_payload(200, {
        "status": "openbrain vercel api online",
        "db_hostname": hostname,
        "resolved_ipv4": ipv4,
        "conninfo_preview": conninfo_preview,
        "db_connect": "ok" if conn else err,
    })
