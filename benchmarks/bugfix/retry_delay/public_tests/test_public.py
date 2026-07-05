from retry_delay import retry_delay

def test_exponential_delay():
    assert retry_delay(0) == 1
    assert retry_delay(2, base=2) == 8
