import pytest
from dependency_order import dependency_order

def test_includes_leaf_dependencies_and_detects_cycles():
    assert dependency_order({'app': ['lib'], 'lib': ['core']}) == ['core', 'lib', 'app']
    with pytest.raises(ValueError):
        dependency_order({'a': ['b'], 'b': ['a']})
