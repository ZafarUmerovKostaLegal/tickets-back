"""Unit tests for monthly hour package billing (N for $X + overage)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from support.service_path import ensure_service_in_path


def _entry(eid: str, work_date: date, hours: float, *, billable: bool = True):
    return SimpleNamespace(
        id=eid,
        work_date=work_date,
        hours=Decimal(str(hours)),
        is_billable=billable,
        voided_at=None,
        auth_user_id=1,
        project_id="p1",
    )


def test_jan_carry_feb_current_first_burns_rollover():
    ensure_service_in_path("time_tracking")
    from application.package_billing import attribute_entries_for_months, walk_months

    n = Decimal("10")
    # Jan 8 → carry 2; Feb used 10 → current-first uses 10 of Feb, Jan 2 expires
    used = {(2026, 1): Decimal("8"), (2026, 2): Decimal("10")}
    months = [(2026, 1), (2026, 2)]
    summaries = walk_months(
        package_hours=n,
        package_fee=Decimal("2000"),
        used_by_month=used,
        months=months,
    )
    assert summaries[0].carry_out == Decimal("2")
    assert summaries[0].overage_hours == Decimal("0")
    assert summaries[1].carried_in == Decimal("2")
    assert summaries[1].capacity == Decimal("12")
    assert summaries[1].used_from_current == Decimal("10")
    assert summaries[1].used_from_rollover == Decimal("0")
    assert summaries[1].expired_rollover == Decimal("2")
    assert summaries[1].carry_out == Decimal("0")
    assert summaries[1].overage_hours == Decimal("0")

    entries = [
        _entry("a", date(2026, 1, 5), 8),
        _entry("b", date(2026, 2, 3), 10),
    ]
    _, splits = attribute_entries_for_months(
        package_hours=n, entries=entries, months=months
    )
    assert splits["a"].covered_hours == Decimal("8")
    assert splits["a"].overage_hours == Decimal("0")
    assert splits["b"].covered_hours == Decimal("10")
    assert splits["b"].overage_hours == Decimal("0")


def test_zero_jan_full_carry_to_feb():
    ensure_service_in_path("time_tracking")
    from application.package_billing import walk_months

    summaries = walk_months(
        package_hours=Decimal("10"),
        package_fee=Decimal("2000"),
        used_by_month={(2026, 1): Decimal("0"), (2026, 2): Decimal("15")},
        months=[(2026, 1), (2026, 2)],
    )
    assert summaries[0].carry_out == Decimal("10")
    assert summaries[1].capacity == Decimal("20")
    assert summaries[1].used_from_current == Decimal("10")
    assert summaries[1].used_from_rollover == Decimal("5")
    assert summaries[1].expired_rollover == Decimal("5")
    assert summaries[1].overage_hours == Decimal("0")
    assert summaries[1].carry_out == Decimal("0")


def test_entry_straddles_package_boundary():
    ensure_service_in_path("time_tracking")
    from application.package_billing import attribute_entries_for_months

    # One 12h entry in a month with N=10 → 10 covered, 2 overage
    entries = [_entry("x", date(2026, 3, 1), 12)]
    summaries, splits = attribute_entries_for_months(
        package_hours=Decimal("10"),
        entries=entries,
        months=[(2026, 3)],
    )
    assert summaries[0].overage_hours == Decimal("2")
    assert splits["x"].covered_hours == Decimal("10")
    assert splits["x"].overage_hours == Decimal("2")


def test_chrono_entries_overage_on_later_entry():
    ensure_service_in_path("time_tracking")
    from application.package_billing import attribute_entries_for_months

    entries = [
        _entry("early", date(2026, 4, 1), 7),
        _entry("late", date(2026, 4, 10), 5),
    ]
    _, splits = attribute_entries_for_months(
        package_hours=Decimal("10"),
        entries=entries,
        months=[(2026, 4)],
    )
    assert splits["early"].covered_hours == Decimal("7")
    assert splits["early"].overage_hours == Decimal("0")
    assert splits["late"].covered_hours == Decimal("3")
    assert splits["late"].overage_hours == Decimal("2")


def test_tm_project_not_package():
    ensure_service_in_path("time_tracking")
    from application.package_billing import (
        compute_entry_splits_for_project_entries,
        is_hour_package_project,
    )

    p = SimpleNamespace(
        project_type="time_and_materials",
        package_hours_per_month=Decimal("10"),
        package_fee_amount=Decimal("2000"),
    )
    assert not is_hour_package_project(p)
    summaries, splits = compute_entry_splits_for_project_entries(
        p, [_entry("a", date(2026, 1, 1), 5)]
    )
    assert summaries == []
    assert splits == {}


def test_build_tt_upsert_stub_regression_still_ok():
    """Smoke: dual-write stubs unrelated but keep import path healthy."""
    ensure_service_in_path("gateway")
    from presentation.time_tracking_user_provision import build_tt_upsert_payload_from_auth_record

    payload = build_tt_upsert_payload_from_auth_record(
        {"id": 1, "email": "a@b.c", "time_tracking_role": "user"}
    )
    assert payload["email"] == "auth-user-1@tt.local"
