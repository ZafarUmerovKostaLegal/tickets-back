

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application import entry_pricing as ep
from application.money_amounts import money_product_hours_rate


def _rate(
    rid: str,
    amount: str,
    cur: str = "USD",
    vf: date | None = None,
    vt: date | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        amount=Decimal(amount),
        currency=cur,
        valid_from=vf,
        valid_to=vt,
    )


def test_cost_amount_multiply_and_round_half_up() -> None:
    r = _rate("1", "100.00", "USD", date(2024, 1, 1), date(2024, 12, 31))
    amt, rph, cur = ep._cost_amount_for_entry(
        Decimal("1.5"), date(2024, 6, 1), [r], project_currency="USD"
    )
    assert cur == "USD"
    assert rph == Decimal("100.00")
    assert amt == Decimal("150.00")


def test_cost_amount_no_rates() -> None:
    amt, rph, cur = ep._cost_amount_for_entry(
        Decimal("2"), date(2024, 1, 1), None, project_currency="USD"
    )
    assert amt == Decimal(0) and rph is None and cur == "USD"


def test_billable_rate_for_entry() -> None:
    r = _rate("1", "80.00", "USD", date(2024, 1, 1), date(2024, 12, 31))
    rate, cur = ep._billable_rate_for_entry(date(2024, 3, 1), [r], project_currency="USD")
    assert rate == Decimal("80.00")
    assert cur == "USD"


@pytest.mark.parametrize(
    "hours,rate,expected",
    [
        (Decimal("0.1"), Decimal("9.99"), Decimal("1.00")),
        (Decimal("3"), Decimal("0.125"), Decimal("0.38")),
        (Decimal("0.33"), Decimal("10.00"), Decimal("3.30")),
    ],
)
def test_money_product_hours_rate_half_up(
    hours: Decimal, rate: Decimal, expected: Decimal
) -> None:
    assert money_product_hours_rate(hours, rate) == expected
