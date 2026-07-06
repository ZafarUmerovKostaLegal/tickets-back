from datetime import date
from decimal import Decimal

from application.entry_pricing import _billable_amount_for_entry


class _Rate:
    def __init__(self, amount, currency, valid_from, valid_to, applies_to_project_id=None):
        self.amount = Decimal(str(amount))
        self.currency = currency
        self.valid_from = valid_from
        self.valid_to = valid_to
        self.applies_to_project_id = applies_to_project_id
        self.id = f"{applies_to_project_id}-{valid_from}-{valid_to}"


def test_open_project_scoped_rate_covers_historical_entries():
    bounded = [
        _Rate(100, "USD", date(2025, 1, 1), date(2025, 12, 31), applies_to_project_id="proj-1"),
    ]
    amt_bounded, _ = _billable_amount_for_entry(
        Decimal("2"),
        True,
        date(2024, 6, 1),
        bounded,
        project_currency="USD",
        time_entry_project_id="proj-1",
    )
    assert amt_bounded == Decimal(0)

    open_rates = [_Rate(100, "USD", None, None, applies_to_project_id="proj-1")]
    amt_open, _ = _billable_amount_for_entry(
        Decimal("2"),
        True,
        date(2024, 6, 1),
        open_rates,
        project_currency="USD",
        time_entry_project_id="proj-1",
    )
    assert amt_open == Decimal("200")
