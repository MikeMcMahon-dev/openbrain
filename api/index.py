from __future__ import annotations


def handler(_request):
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/plain"},
        "body": "openbrain probe",
    }
