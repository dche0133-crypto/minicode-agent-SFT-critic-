import pytest
from parse_bool_flag import parse_bool

def test_case_and_whitespace():
    assert parse_bool(' YES ') is True
    assert parse_bool('0') is False

def test_invalid_value():
    with pytest.raises(ValueError):
        parse_bool('sometimes')
