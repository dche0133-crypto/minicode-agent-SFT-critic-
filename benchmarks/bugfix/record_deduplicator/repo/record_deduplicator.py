def deduplicate(records):
    return list({record['id']: record for record in records}.values())
