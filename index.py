def handler(event, context):
    return {
        "statusCode": 200,
        "headers": {"content-type": "text/plain"},
        "body": "openbrain probe",
    }
