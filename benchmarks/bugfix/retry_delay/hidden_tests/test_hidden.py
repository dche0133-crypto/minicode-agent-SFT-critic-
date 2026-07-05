from retry_delay import retry_delay

def test_cap_and_invalid_attempt():
    assert retry_delay(10, base=4, cap=30) == 30
    try:
        retry_delay(-1)
    except ValueError:
        pass
    else:
        raise AssertionError('negative attempts must fail')
