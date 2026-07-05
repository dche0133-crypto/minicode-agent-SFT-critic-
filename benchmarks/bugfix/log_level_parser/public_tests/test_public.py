from log_level_parser import parse_log

def test_parses_level_and_message():
    assert parse_log('INFO: started') == {'level': 'INFO', 'message': 'started'}
