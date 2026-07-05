from csv_row_parser import parse_row

def test_plain_and_quoted_cells():
    assert parse_row('a, b, c') == ['a', 'b', 'c']
    assert parse_row('a,"b,c",d') == ['a', 'b,c', 'd']
