from record_deduplicator import deduplicate

def test_keeps_last_record_with_normalized_id():
    records = [{'id': ' A ', 'value': 1}, {'id': 'b', 'value': 2}, {'id': 'a', 'value': 3}]
    assert deduplicate(records) == [{'id': 'a', 'value': 3}, {'id': 'b', 'value': 2}]
