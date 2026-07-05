from config_defaults import apply_defaults

def test_adds_top_level_defaults_without_mutating_input():
    source = {'timeout': 5}
    result = apply_defaults(source)
    assert result == {'retries': 3, 'timeout': 5, 'database': {'port': 5432}}
    assert source == {'timeout': 5}
