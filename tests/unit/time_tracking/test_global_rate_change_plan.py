from __future__ import annotations

from datetime import date
from decimal import Decimal

from application.entry_pricing import _billable_amount_for_entry
from application.hourly_rate_logic import build_rate_change_plan


class _Rate:
    def __init__(self, amount, currency, valid_from, valid_to, applies_to_project_id=None, rid=""):
        self.amount = Decimal(str(amount))
        self.currency = currency
        self.valid_from = valid_from
        self.valid_to = valid_to
        self.applies_to_project_id = applies_to_project_id
        self.id = rid or f"g-{valid_from}-{valid_to}"


def test_global_rate_change_closes_open_period() -> None:
    global_rates = [_Rate(100, "USD", None, None, rid="g1")]
    plan = build_rate_change_plan(global_rates, [], date(2025, 5, 1))

    assert plan.update_existing_id is None
    assert plan.close_existing_id == "g1"
    assert plan.close_valid_to == date(2025, 4, 30)
    assert plan.create_new is True


def test_global_rate_split_pricing() -> None:
    rates = [
        _Rate(100, "USD", None, date(2025, 4, 30), rid="old"),
        _Rate(150, "USD", date(2025, 5, 1), None, rid="new"),
    ]
    before, _ = _billable_amount_for_entry(
        Decimal("2"), True, date(2025, 3, 1), rates,
        project_currency="USD", time_entry_project_id="proj-x",
    )
    after, _ = _billable_amount_for_entry(
        Decimal("2"), True, date(2025, 6, 1), rates,
        project_currency="USD", time_entry_project_id="proj-x",
    )
    assert before == Decimal("200")
    assert after == Decimal("300")
