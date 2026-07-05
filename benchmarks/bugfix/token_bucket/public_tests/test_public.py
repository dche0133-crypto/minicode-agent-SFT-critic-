from token_bucket import TokenBucket

def test_consumes_only_available_tokens():
    bucket = TokenBucket(3)
    assert bucket.consume(2)
    assert bucket.tokens == 1
    assert not bucket.consume(2)
    assert bucket.tokens == 1
