import pytest
from parse_bool_flag import parse_bool

def test_common_values():
    assert parse_bool('true') is True
    assert parse_bool('false') is False

def test_boolean_input():
    assert parse_bool(True) is True
