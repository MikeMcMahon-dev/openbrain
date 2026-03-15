from __future__ import annotations


def handler(_request):
    return {
        "statusCode": 200,
        "headers": {"content-type": "text/plain"},
        "body": "openbrain ping",
    }
