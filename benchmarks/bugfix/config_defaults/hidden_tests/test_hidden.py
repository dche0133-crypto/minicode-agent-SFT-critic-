from config_defaults import apply_defaults

def test_preserves_nested_values():
    assert apply_defaults({'database': {'port': 3306}})['database'] == {'port': 3306}
