from datetime import date
import pytest
from date_window import days_between

def test_leap_day_and_reverse_range():
    assert days_between(date(2024, 2, 28), date(2024, 3, 1)) == 3
    with pytest.raises(ValueError):
        days_between(date(2024, 3, 1), date(2024, 2, 28))
