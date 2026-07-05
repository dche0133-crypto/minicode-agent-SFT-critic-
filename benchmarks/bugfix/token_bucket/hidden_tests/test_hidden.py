import pytest
from token_bucket import TokenBucket

def test_rejects_invalid_amounts_and_respects_capacity():
    bucket = TokenBucket(2, tokens=5)
    assert bucket.tokens == 2
    with pytest.raises(ValueError):
        bucket.consume(0)
