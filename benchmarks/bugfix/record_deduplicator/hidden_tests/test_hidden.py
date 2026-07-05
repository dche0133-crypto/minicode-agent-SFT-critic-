import pytest
from record_deduplicator import deduplicate

def test_normalizes_ids_and_rejects_missing_id():
    assert deduplicate([{'id': ' A ', 'value': 1}, {'id': 'a', 'value': 2}]) == [{'id': 'a', 'value': 2}]
    with pytest.raises(ValueError):
        deduplicate([{'value': 1}])
