import pytest
from log_level_parser import parse_log

def test_normalizes_and_rejects_unknown_level():
    assert parse_log(' warning : disk: 90% ') == {'level': 'WARNING', 'message': 'disk: 90%'}
    with pytest.raises(ValueError):
        parse_log('TRACE: detail')
