from csv_row_parser import parse_row

def test_quoted_commas_and_escaped_quotes():
    assert parse_row('a,"b,c","say ""hi"""') == ['a', 'b,c', 'say "hi"']
