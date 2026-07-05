import pytest

from config_records import parse_records


def test_parses_records_and_ignores_comments():
    text = """
    # service configuration
    workers=4

    retries=2
    """

    assert parse_records(text) == {"workers": 4, "retries": 2}


def test_trims_whitespace_around_name_and_value():
    assert parse_records(" workers = 4 \n retries= 2 ") == {"workers": 4, "retries": 2}


def test_reports_line_number_for_missing_separator():
    with pytest.raises(ValueError, match="line 2"):
        parse_records("workers=4\nretries")
