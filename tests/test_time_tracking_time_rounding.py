

from decimal import Decimal

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application.time_rounding import (
    hours_from_seconds,
    quantize_seconds_to_minute,
    resolve_duration_for_entry,
    seconds_from_hours,
)
from infrastructure.repository_shared import normalize_time_entry_hours


@pytest.mark.parametrize(
    "seconds,expected_minute_seconds",
    [
        (0, 0),
        (29, 0),
        (30, 60),
        (59, 60),
        (60, 60),
        (90, 120),
        (4053, 4080),
    ],
)
def test_quantize_seconds_to_minute(seconds: int, expected_minute_seconds: int) -> None:
    assert quantize_seconds_to_minute(seconds) == expected_minute_seconds


def test_hours_from_seconds_one_hour() -> None:
    assert hours_from_seconds(3600) == Decimal("1")
    assert hours_from_seconds(3660) == Decimal("1.016667").quantize(Decimal("0.000001"))


def test_seconds_from_hours_round_trip() -> None:
    assert seconds_from_hours(Decimal("1")) == 3600
    assert seconds_from_hours(Decimal("0.5")) == 1800


def test_normalize_time_entry_hours_rejects_nonpositive() -> None:
    with pytest.raises(ValueError, match="больше нуля"):
        normalize_time_entry_hours(Decimal("0"))
    with pytest.raises(ValueError, match="больше нуля"):
        normalize_time_entry_hours(Decimal("0.0000001"))


def test_resolve_duration_for_entry_from_seconds() -> None:
    assert resolve_duration_for_entry(3600, None) == 3600
    assert resolve_duration_for_entry(90, None) == 120


def test_resolve_duration_rejects_subminute_after_quantize() -> None:
    with pytest.raises(ValueError, match="не меньше 1"):
        resolve_duration_for_entry(29, None)

