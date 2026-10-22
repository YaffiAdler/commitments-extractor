from datetime import date

import pytest

from app.pipeline.normalize import parse_date


@pytest.mark.parametrize("text, expected", [
    ("01.02.2025", date(2025, 2, 1)),
    ("1/2/2025", date(2025, 2, 1)),
    (" 01 - 02 - 2025 ", date(2025, 2, 1)),
    ("2025-02-01", date(2025, 2, 1)),
])
def test_full_dates_day_first(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text", ["31.8", "-31.8", "01.02.25", "31.02.2025", "abc", ""])
def test_partial_or_invalid_dates_are_not_completed(text):
    assert parse_date(text) is None
