from rolling_rate_limit import allow

def test_allows_under_limit_and_discards_expired():
    assert allow([8, 9], now=10, limit=3, window=5)
    assert not allow([8, 9, 10], now=10, limit=3, window=5)
    assert allow([1, 2, 8], now=10, limit=2, window=5)
