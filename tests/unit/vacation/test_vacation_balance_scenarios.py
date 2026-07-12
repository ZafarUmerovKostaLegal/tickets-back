"""Scenario tests: 14-day continuous portion, 3-week leave, over-entitlement."""

from datetime import date

import pytest

from support.service_path import ensure_service_in_path


def _fresh(entitled: int = 28) -> object:
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance

    return VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=entitled,
        used_days=0,
        pending_days=0,
        remaining_days=entitled,
        continuous_14_satisfied=False,
        min_continuous_days=14,
    )


def test_fourteen_days_counts_as_paid_and_satisfies_continuous():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        count_calendar_days_inclusive,
        simulate_balance_after_consume,
        validate_annual_vacation_request,
    )

    bal = _fresh()
    d0, d1 = date(2026, 6, 1), date(2026, 6, 14)
    days = count_calendar_days_inclusive(d0, d1)
    assert days == 14

    validate_annual_vacation_request(
        date_from=d0,
        date_to=d1,
        days_count=days,
        balances_by_year={2026: bal},
    )

    pending = simulate_balance_after_consume(bal, days=days, as_approved=False)
    assert pending.pending_days == 14
    assert pending.remaining_days == 14
    assert pending.continuous_14_satisfied is False  # only after approve

    approved = simulate_balance_after_consume(bal, days=days, as_approved=True)
    assert approved.used_days == 14
    assert approved.remaining_days == 14
    assert approved.continuous_14_satisfied is True


def test_three_weeks_is_paid_annual_not_unpaid_when_within_entitlement():
    """21 календарных дней при остатке 28 — это оплачиваемый ежегодный, не неоплачиваемый."""
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        count_calendar_days_inclusive,
        simulate_balance_after_consume,
        validate_annual_vacation_request,
    )

    bal = _fresh(28)
    d0, d1 = date(2026, 7, 1), date(2026, 7, 21)
    days = count_calendar_days_inclusive(d0, d1)
    assert days == 21

    validate_annual_vacation_request(
        date_from=d0,
        date_to=d1,
        days_count=days,
        balances_by_year={2026: bal},
    )

    after = simulate_balance_after_consume(bal, days=days, as_approved=True)
    assert after.used_days == 21
    assert after.remaining_days == 7
    assert after.continuous_14_satisfied is True
    # Всё списано с оплачиваемого остатка — unpaid не появляется


def test_three_weeks_rejected_when_only_14_remaining_not_converted_to_unpaid():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        VacationBalance,
        count_calendar_days_inclusive,
        validate_annual_vacation_request,
    )

    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=14,
        pending_days=0,
        remaining_days=14,
        continuous_14_satisfied=True,
        min_continuous_days=14,
    )
    d0, d1 = date(2026, 8, 1), date(2026, 8, 21)
    days = count_calendar_days_inclusive(d0, d1)
    assert days == 21

    with pytest.raises(ValueError, match="неоплачиваемый"):
        validate_annual_vacation_request(
            date_from=d0,
            date_to=d1,
            days_count=days,
            balances_by_year={2026: bal},
        )


def test_after_14_used_short_parts_ok_up_to_remaining():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        VacationBalance,
        simulate_balance_after_consume,
        validate_annual_vacation_request,
    )

    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=14,
        pending_days=0,
        remaining_days=14,
        continuous_14_satisfied=True,
        min_continuous_days=14,
    )
    for length, d_to in ((1, date(2026, 9, 1)), (2, date(2026, 9, 2)), (5, date(2026, 9, 5)), (7, date(2026, 9, 7))):
        validate_annual_vacation_request(
            date_from=date(2026, 9, 1),
            date_to=d_to,
            days_count=length,
            balances_by_year={2026: bal},
        )

    after7 = simulate_balance_after_consume(bal, days=7, as_approved=True)
    assert after7.used_days == 21
    assert after7.remaining_days == 7


def test_pending_reserves_balance_so_second_overcommit_fails():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        VacationBalance,
        validate_annual_vacation_request,
    )

    # Already have 20 pending of 28 → remaining 8
    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=0,
        pending_days=20,
        remaining_days=8,
        continuous_14_satisfied=False,
        min_continuous_days=14,
    )
    # continuous not satisfied and 8 < 14 → continuous rule first
    with pytest.raises(ValueError, match="обязательная непрерывная часть"):
        validate_annual_vacation_request(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 8),
            days_count=8,
            balances_by_year={2026: bal},
        )


def test_second_pending_14_ok_when_first_pending_14():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        VacationBalance,
        validate_annual_vacation_request,
    )

    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=0,
        pending_days=14,
        remaining_days=14,
        continuous_14_satisfied=False,
        min_continuous_days=14,
    )
    validate_annual_vacation_request(
        date_from=date(2026, 11, 1),
        date_to=date(2026, 11, 14),
        days_count=14,
        balances_by_year={2026: bal},
    )


def test_year_split_counts_each_year_against_own_balance():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        VacationBalance,
        count_calendar_days_inclusive,
        days_of_period_in_year,
        validate_annual_vacation_request,
    )

    d0, d1 = date(2025, 12, 20), date(2026, 1, 10)
    assert count_calendar_days_inclusive(d0, d1) == 22
    assert days_of_period_in_year(d0, d1, 2025) == 12
    assert days_of_period_in_year(d0, d1, 2026) == 10

    bal_2025 = VacationBalance(
        year=2025,
        employee_user_id=1,
        entitled_days=28,
        used_days=20,
        pending_days=0,
        remaining_days=8,
        continuous_14_satisfied=True,
        min_continuous_days=14,
    )
    bal_2026 = _fresh(28)

    with pytest.raises(ValueError, match="2025"):
        validate_annual_vacation_request(
            date_from=d0,
            date_to=d1,
            days_count=22,
            balances_by_year={2025: bal_2025, 2026: bal_2026},
        )
