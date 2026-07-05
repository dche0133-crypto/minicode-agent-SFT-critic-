import pytest
from service import request_options

def test_rejects_invalid_values_and_keeps_zero_semantics_explicit():
    with pytest.raises(ValueError):
        request_options({'timeout': 0})
    with pytest.raises(ValueError):
        request_options({'retries': -1})
