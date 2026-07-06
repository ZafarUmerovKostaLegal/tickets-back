from __future__ import annotations

from datetime import date
from decimal import Decimal

from application.entry_pricing import _billable_amount_for_entry
from application.hourly_rate_logic import build_rate_change_plan, plan_overlapping_reconcile


class _Rate:
    def __init__(self, amount, currency, valid_from, valid_to, applies_to_project_id=None, rid=""):
        self.amount = Decimal(str(amount))
        self.currency = currency
        self.valid_from = valid_from
        self.valid_to = valid_to
        self.applies_to_project_id = applies_to_project_id
        self.id = rid or f"{applies_to_project_id}-{valid_from}"


def test_first_project_override_snapshots_global_before() -> None:
                                                                                     
    global_rates = [_Rate(100, "USD", None, None, rid="g1")]
    project_rates: list = []
    plan = build_rate_change_plan(project_rates, global_rates, date(2024, 1, 1))

    assert plan.update_existing_id is None
    assert plan.close_existing_id is None
    assert plan.create_before_amount == Decimal("100")
    assert plan.create_before_valid_to == date(2023, 12, 31)
    assert plan.create_new is True
    assert plan.create_new_valid_to is None


def test_existing_open_project_rate_is_closed() -> None:
    project_rates = [_Rate(150, "USD", None, None, applies_to_project_id="proj-A", rid="p1")]
    plan = build_rate_change_plan(project_rates, [], date(2024, 6, 1))

    assert plan.close_existing_id == "p1"
    assert plan.close_valid_to == date(2024, 5, 31)
    assert plan.create_before_amount is None
    assert plan.create_new_valid_to is None


def test_change_exactly_on_period_start_updates_in_place() -> None:
    project_rates = [
        _Rate(150, "USD", date(2024, 1, 1), None, applies_to_project_id="proj-A", rid="p1"),
    ]
    plan = build_rate_change_plan(project_rates, [], date(2024, 1, 1))

    assert plan.update_existing_id == "p1"
    assert plan.create_new is False
    assert plan.close_existing_id is None


def test_pricing_after_first_override_old_before_new_after() -> None:
                                                                          
    rates = [
        _Rate(100, "USD", None, None, rid="g1"),                       
        _Rate(100, "USD", None, date(2023, 12, 31), applies_to_project_id="proj-A", rid="b1"),
        _Rate(140, "USD", date(2024, 1, 1), None, applies_to_project_id="proj-A", rid="n1"),
    ]
    before_amt, _ = _billable_amount_for_entry(
        Decimal("1"), True, date(2023, 6, 1), rates,
        project_currency="USD", time_entry_project_id="proj-A",
    )
    after_amt, _ = _billable_amount_for_entry(
        Decimal("1"), True, date(2024, 3, 1), rates,
        project_currency="USD", time_entry_project_id="proj-A",
    )
    assert before_amt == Decimal("100")
    assert after_amt == Decimal("140")


def test_new_period_bounded_by_future_rate() -> None:
    project_rates = [
        _Rate(150, "USD", None, date(2023, 12, 31), applies_to_project_id="proj-A", rid="p1"),
        _Rate(200, "USD", date(2025, 1, 1), None, applies_to_project_id="proj-A", rid="p2"),
    ]
    plan = build_rate_change_plan(project_rates, [], date(2024, 6, 1))

    assert plan.close_existing_id is None
    assert plan.create_new_valid_to == date(2024, 12, 31)


def test_reconcile_multiple_open_project_rates_closes_one_deletes_rest() -> None:
    project_rates = [
        _Rate(120, "USD", None, None, applies_to_project_id="proj-A", rid="p-low"),
        _Rate(150, "USD", None, None, applies_to_project_id="proj-A", rid="p-mid"),
        _Rate(100, "USD", None, None, applies_to_project_id="proj-A", rid="p-high"),
    ]
    actions = plan_overlapping_reconcile(project_rates, date(2024, 6, 1), None)

    closes = [a for a in actions if a.kind == "close"]
    deletes = [a for a in actions if a.kind == "delete"]
    assert len(closes) == 1
    assert closes[0].rate_id == "p-mid"
    assert closes[0].valid_to == date(2024, 5, 31)
    assert {a.rate_id for a in deletes} == {"p-low", "p-high"}


def test_reconcile_prefers_explicit_keeper_rate_id() -> None:
    project_rates = [
        _Rate(120, "USD", None, None, applies_to_project_id="proj-A", rid="p-low"),
        _Rate(150, "USD", None, None, applies_to_project_id="proj-A", rid="p-high"),
    ]
    actions = plan_overlapping_reconcile(
        project_rates,
        date(2024, 6, 1),
        None,
        keeper_rate_id="p-low",
    )
    closes = [a for a in actions if a.kind == "close"]
    deletes = [a for a in actions if a.kind == "delete"]
    assert len(closes) == 1
    assert closes[0].rate_id == "p-low"
    assert {a.rate_id for a in deletes} == {"p-high"}


def test_reconcile_after_close_pricing_uses_closed_amount_before_change() -> None:
    project_rates = [
        _Rate(120, "USD", None, None, applies_to_project_id="proj-A", rid="p-low"),
        _Rate(150, "USD", None, None, applies_to_project_id="proj-A", rid="p-high"),
    ]
    actions = plan_overlapping_reconcile(project_rates, date(2024, 6, 1), None)
    by_id = {r.id: r for r in project_rates}
    for action in actions:
        if action.kind == "close":
            by_id[action.rate_id].valid_to = action.valid_to
        else:
            del by_id[action.rate_id]
    by_id["p-new"] = _Rate(180, "USD", date(2024, 6, 1), None, applies_to_project_id="proj-A", rid="p-new")
    rates = list(by_id.values()) + [
        _Rate(100, "USD", None, None, rid="g1"),
    ]
    before_amt, _ = _billable_amount_for_entry(
        Decimal("1"), True, date(2024, 3, 1), rates,
        project_currency="USD", time_entry_project_id="proj-A",
    )
    after_amt, _ = _billable_amount_for_entry(
        Decimal("1"), True, date(2024, 7, 1), rates,
        project_currency="USD", time_entry_project_id="proj-A",
    )
    assert before_amt == Decimal("120")
    assert after_amt == Decimal("180")
