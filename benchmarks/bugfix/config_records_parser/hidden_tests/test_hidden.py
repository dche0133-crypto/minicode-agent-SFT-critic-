import pytest

from config_records import parse_records


@pytest.mark.parametrize(
    "text, line_number",
    [
        ("=4", 1),
        ("workers=", 1),
        ("workers=-1", 1),
        ("workers=4=5", 1),
        ("# ignored\n\nworkers=abc", 3),
    ],
)
def test_rejects_invalid_records_with_original_line_number(text, line_number):
    with pytest.raises(ValueError, match=rf"line {line_number}"):
        parse_records(text)


def test_duplicate_names_use_last_value_after_normalization():
    assert parse_records(" workers = 1\nworkers=3") == {"workers": 3}
