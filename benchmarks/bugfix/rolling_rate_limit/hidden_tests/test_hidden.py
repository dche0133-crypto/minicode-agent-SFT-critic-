import pytest
from rolling_rate_limit import allow

def test_discards_expired_and_validates_inputs():
    assert allow([1, 2, 8], now=10, limit=2, window=5)
    with pytest.raises(ValueError):
        allow([], now=1, limit=0, window=5)
