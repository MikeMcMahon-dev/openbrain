from __future__ import annotations

import socket
from unittest.mock import patch

from api._openbrain_api import _source_reachable, _url_destination_safe


def _addresses(*values):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 80)) for value in values]


def test_private_literal_is_rejected():
    ok, reason = _url_destination_safe("http://192.168.1.20/secret")
    assert not ok
    assert "private" in reason


def test_hostname_resolving_to_private_address_is_rejected():
    with patch("api._openbrain_api.socket.getaddrinfo", return_value=_addresses("10.0.0.7")):
        ok, reason = _source_reachable("url", "https://internal.example/secret")
    assert not ok
    assert "private" in reason


def test_public_hostname_is_allowed_past_destination_check():
    with patch("api._openbrain_api.socket.getaddrinfo", return_value=_addresses("8.8.8.8")), \
         patch("api._openbrain_api.urllib.request.urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.status = 200
        ok, reason = _source_reachable("url", "https://public.example/document")
    assert ok, reason
