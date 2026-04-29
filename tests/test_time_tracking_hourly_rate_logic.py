

from datetime import date
from types import SimpleNamespace

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application.hourly_rate_logic import (
    filter_rates_by_currency,
    intervals_overlap,
    pick_rate_for_date,
    validate_range_order,
)


def test_intervals_overlap_inclusive() -> None:
    assert intervals_overlap(date(2024, 1, 1), date(2024, 1, 31), date(2024, 1, 15), date(2024, 2, 1))
    assert not intervals_overlap(date(2024, 1, 1), date(2024, 1, 10), date(2024, 1, 11), date(2024, 1, 20))
    assert intervals_overlap(None, None, date(2024, 1, 1), date(2024, 1, 1))


def test_validate_range_order() -> None:
    validate_range_order(date(2024, 1, 1), date(2024, 1, 2))
    with pytest.raises(ValueError, match="позже"):
        validate_range_order(date(2024, 2, 1), date(2024, 1, 1))


def test_filter_rates_by_currency() -> None:
    a = SimpleNamespace(currency="USD")
    b = SimpleNamespace(currency="EUR")
    assert filter_rates_by_currency([a, b], "eur") == [b]


def test_pick_rate_for_date() -> None:
    r1 = SimpleNamespace(
        id="a",
        valid_from=date(2024, 1, 1),
        valid_to=date(2024, 6, 30),
    )
    r2 = SimpleNamespace(
        id="b",
        valid_from=date(2024, 7, 1),
        valid_to=None,
    )
    assert pick_rate_for_date([r1, r2], date(2024, 3, 15)) is r1
    assert pick_rate_for_date([r1, r2], date(2024, 8, 1)) is r2
