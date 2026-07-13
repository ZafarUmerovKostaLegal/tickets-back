"""Flat fee per time entry (e.g. My mehnat registration = 230000 UZS)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from application.entry_pricing import (
    _billable_amount_for_entry,
    _billable_rate_for_entry,
    billable_amount_respecting_package,
)
from application.package_billing import entry_counts_toward_hour_package
from application.task_billing import (
    BILLING_MODE_FLAT_FEE,
    MEHNAT_FLAT_FEE_AMOUNT,
    MEHNAT_FLAT_FEE_CURRENCY,
    is_flat_fee_task,
)


def _mehnat_task():
    return SimpleNamespace(
        billing_mode=BILLING_MODE_FLAT_FEE,
        flat_fee_amount=MEHNAT_FLAT_FEE_AMOUNT,
        flat_fee_currency=MEHNAT_FLAT_FEE_CURRENCY,
        name="My mehnat registration",
    )


def test_flat_fee_is_per_entry_not_hours():
    task = _mehnat_task()
    assert is_flat_fee_task(task)
    amt1, cur1 = _billable_amount_for_entry(
        Decimal("0.25"),
        True,
        date(2026, 7, 1),
        None,
        project_currency="USD",
        task=task,
    )
    amt2, cur2 = _billable_amount_for_entry(
        Decimal("3"),
        True,
        date(2026, 7, 1),
        None,
        project_currency="USD",
        task=task,
    )
    assert amt1 == MEHNAT_FLAT_FEE_AMOUNT
    assert amt2 == MEHNAT_FLAT_FEE_AMOUNT
    assert cur1 == "UZS"
    assert cur2 == "UZS"


def test_flat_fee_zero_when_non_billable():
    task = _mehnat_task()
    amt, cur = _billable_amount_for_entry(
        Decimal("1"),
        False,
        date(2026, 7, 1),
        None,
        task=task,
    )
    assert amt == Decimal(0)
    assert cur == "UZS"


def test_flat_fee_rate_equals_amount():
    task = _mehnat_task()
    rate, cur = _billable_rate_for_entry(date(2026, 7, 1), None, task=task)
    assert rate == MEHNAT_FLAT_FEE_AMOUNT
    assert cur == "UZS"


def test_flat_fee_ignores_package_overage_split():
    task = _mehnat_task()
    split = SimpleNamespace(overage_hours=Decimal(0), covered_hours=Decimal("1"))
    amt, cur = billable_amount_respecting_package(
        Decimal("1"),
        True,
        date(2026, 7, 1),
        None,
        project_currency="USD",
        task=task,
        package_split=split,
    )
    assert amt == MEHNAT_FLAT_FEE_AMOUNT
    assert cur == "UZS"


def test_flat_fee_entry_does_not_count_toward_hour_package():
    task = _mehnat_task()
    entry = SimpleNamespace(task_id="t1")
    assert entry_counts_toward_hour_package(entry, {"t1": task}) is False
    assert entry_counts_toward_hour_package(entry, {}) is True
