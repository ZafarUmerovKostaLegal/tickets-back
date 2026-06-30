from __future__ import annotations

from datetime import date
from decimal import Decimal

from application.entry_pricing import (
    _billable_amount_for_entry,
    billable_scoped_user_rates,
    pick_billable_rate_for_entry,
)
from application.hourly_rate_logic import pick_rate_for_date


class _Rate:
    def __init__(self, amount, currency, valid_from, valid_to, applies_to_project_id=None, rid=""):
        self.amount = Decimal(str(amount))
        self.currency = currency
        self.valid_from = valid_from
        self.valid_to = valid_to
        self.applies_to_project_id = applies_to_project_id
        self.id = rid or f"{applies_to_project_id}-{valid_from}"


def test_project_rate_overrides_global() -> None:
    rates = [
        _Rate(100, "EUR", None, None),  # global
        _Rate(180, "EUR", None, None, applies_to_project_id="proj-A"),  # per-project
    ]
    scoped = billable_scoped_user_rates(rates, "EUR", "proj-A")
    assert len(scoped) == 1
    assert scoped[0].amount == Decimal("180")


def test_global_used_when_no_project_rate() -> None:
    rates = [
        _Rate(100, "EUR", None, None),
        _Rate(180, "EUR", None, None, applies_to_project_id="proj-A"),
    ]
    scoped = billable_scoped_user_rates(rates, "EUR", "proj-B")
    assert len(scoped) == 1
    assert scoped[0].amount == Decimal("100")


def test_multiple_project_intervals_pick_by_date() -> None:
    rates = [
        _Rate(120, "EUR", date(2023, 1, 1), date(2023, 6, 30), applies_to_project_id="proj-A"),
        _Rate(180, "EUR", date(2023, 7, 1), None, applies_to_project_id="proj-A"),
    ]
    scoped = billable_scoped_user_rates(rates, "EUR", "proj-A")
    assert len(scoped) == 2
    early = pick_rate_for_date(scoped, date(2023, 3, 15))
    late = pick_rate_for_date(scoped, date(2024, 1, 1))
    assert early.amount == Decimal("120")
    assert late.amount == Decimal("180")


def test_billable_amount_uses_project_interval() -> None:
    rates = [
        _Rate(120, "EUR", date(2023, 1, 1), date(2023, 6, 30), applies_to_project_id="proj-A"),
        _Rate(180, "EUR", date(2023, 7, 1), None, applies_to_project_id="proj-A"),
    ]
    amt_early, cur = _billable_amount_for_entry(
        Decimal("2"), True, date(2023, 3, 1), rates,
        project_currency="EUR", time_entry_project_id="proj-A",
    )
    amt_late, _ = _billable_amount_for_entry(
        Decimal("2"), True, date(2024, 1, 1), rates,
        project_currency="EUR", time_entry_project_id="proj-A",
    )
    assert amt_early == Decimal("240")
    assert amt_late == Decimal("360")
    assert cur == "EUR"


def test_project_rate_closed_falls_back_to_global() -> None:
    rates = [
        _Rate(100, "EUR", None, None),
        _Rate(180, "EUR", date(2023, 1, 1), date(2023, 6, 30), applies_to_project_id="proj-A"),
    ]
    rate = pick_billable_rate_for_entry(
        date(2024, 1, 1),
        rates,
        project_currency="EUR",
        time_entry_project_id="proj-A",
    )
    assert rate is not None
    assert rate.amount == Decimal("100")


def test_global_rate_change_intervals_in_reports() -> None:
    rates = [
        _Rate(100, "USD", None, date(2025, 4, 30)),
        _Rate(150, "USD", date(2025, 5, 1), None),
    ]
    before, _ = _billable_amount_for_entry(
        Decimal("1"), True, date(2025, 4, 15), rates,
        project_currency="USD", time_entry_project_id=None,
    )
    after, _ = _billable_amount_for_entry(
        Decimal("1"), True, date(2025, 5, 15), rates,
        project_currency="USD", time_entry_project_id=None,
    )
    assert before == Decimal("100")
    assert after == Decimal("150")
