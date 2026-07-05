from datetime import date
from date_window import days_between

def test_inclusive_window():
    assert days_between(date(2024, 1, 1), date(2024, 1, 1)) == 1
    assert days_between(date(2024, 1, 1), date(2024, 1, 3)) == 3
