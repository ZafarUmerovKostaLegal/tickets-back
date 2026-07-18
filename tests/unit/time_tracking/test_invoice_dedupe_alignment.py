"""Duplicate key alignment with report-preview FE."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from support.service_path import ensure_service_in_path


def test_round_decimal_hours_to_minute_matches_fe():
    ensure_service_in_path("time_tracking")
    from application.time_rounding import round_decimal_hours_to_minute

    # 1.008 * 60 = 60.48 → round 60 → 1.0 hour
    assert float(round_decimal_hours_to_minute(Decimal("1.008"))) == 1.0
    # 1.009 * 60 = 60.54 → round 61 → 61/60
    assert abs(float(round_decimal_hours_to_minute(Decimal("1.009"))) - (61 / 60)) < 1e-6
    assert float(round_decimal_hours_to_minute(Decimal("21.183333"))) == 21.183333


def test_invoice_hours_for_billing_matches_excel_num2():
    """Partner Excel: minute-round then Math.round(h*100)/100 — e.g. 2.633333 → 2.63."""
    ensure_service_in_path("time_tracking")
    from application.time_rounding import invoice_hours_for_billing, invoice_rate_for_billing
    from application.money_amounts import money_product_hours_rate

    qty = invoice_hours_for_billing(Decimal("2.633333"))
    assert qty == Decimal("2.63")
    rate = invoice_rate_for_billing(Decimal("150"))
    assert rate == Decimal("150.00")
    assert money_product_hours_rate(qty, rate) == Decimal("394.50")

    assert invoice_hours_for_billing(Decimal("0.75")) == Decimal("0.75")
    assert invoice_hours_for_billing(Decimal("4.65")) == Decimal("4.65")
    assert invoice_hours_for_billing(Decimal("1.033333")) == Decimal("1.03")


def test_ignore_amount_collapses_near_duplicate_entries():
    ensure_service_in_path("time_tracking")
    from application.duplicate_time_entries import deduplicate_entries_for_report

    def entry(eid: str, hours: str, desc: str = "same note"):
        return SimpleNamespace(
            id=eid,
            auth_user_id=7,
            work_date=date(2026, 7, 1),
            task_id="task-1",
            description=desc,
            hours=Decimal(hours),
            rounded_hours=Decimal(hours),
            is_billable=True,
            project_id="proj-1",
            created_at=None,
        )

    a = entry("a", "2.5")
    b = entry("b", "2.5")
    # Different billable amounts would normally keep both if rates differ; ignore_amount collapses.
    projects = {"proj-1": SimpleNamespace(currency="EUR")}
    rates: dict = {}
    kept, dropped = deduplicate_entries_for_report(
        [a, b],
        projects_map=projects,
        rates_map=rates,
        tasks_map={},
        ignore_amount=True,
    )
    assert dropped == 1
    assert len(kept) == 1
    assert kept[0].id == "a"
