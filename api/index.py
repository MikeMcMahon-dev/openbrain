def handler(request, _context=None):
    return {
        "statusCode": 200,
        "headers": {"content-type": "text/plain"},
        "body": "openbrain probe",
    }
