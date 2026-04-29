

from decimal import Decimal

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from infrastructure.repository_shared import normalize_time_entry_hours


def test_ten_seconds_nonzero():
    h = Decimal("10") / Decimal("3600")
    n = normalize_time_entry_hours(h)
    assert n == Decimal("0.002778")
    assert n > 0


def test_subsecond_fraction():
    h = Decimal("1") / Decimal("3600")
    n = normalize_time_entry_hours(h)
    assert n == Decimal("0.000278")


def test_rejects_zero_after_quantize():
    with pytest.raises(ValueError, match="больше нуля"):
        normalize_time_entry_hours(Decimal("0.0000001"))
