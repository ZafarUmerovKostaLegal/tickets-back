

from decimal import Decimal

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application.services.reports._base import _percent_billable


def test_percent_billable() -> None:
    assert _percent_billable(Decimal("10"), Decimal("0")) == 0.0
    assert _percent_billable(Decimal("0"), Decimal("0")) == 0.0
    assert _percent_billable(Decimal("10"), Decimal("5")) == 50.0
    assert _percent_billable(Decimal("3"), Decimal("1")) == 33.3
