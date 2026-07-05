from service import request_options

def test_uses_defaults_and_explicit_values():
    assert request_options({}) == {'timeout': 30, 'retries': 3}
    assert request_options({'timeout': 5, 'retries': 2}) == {'timeout': 5, 'retries': 2}
