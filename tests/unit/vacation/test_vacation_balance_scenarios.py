"""Scenario tests: flexible 7 + continuous 14 + over-entitlement."""

from datetime import date

import pytest

from support.service_path import ensure_service_in_path


def _fresh(entitled: int = 28, *, flex_used: int = 0, continuous: bool = False) -> object:
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance

    flex_max = 7
    used = flex_used if not continuous else max(14, flex_used)
    return VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=entitled,
        used_days=used,
        pending_days=0,
        remaining_days=max(0, entitled - used),
        continuous_14_satisfied=continuous,
        min_continuous_days=14,
        flexible_days_max=flex_max,
        flexible_days_used=min(flex_used, flex_max) if not continuous else flex_max,
        flexible_days_remaining=max(0, flex_max - flex_used) if not continuous else flex_max,
    )


def test_short_parts_within_seven_ok():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        simulate_balance_after_consume,
        validate_annual_vacation_request,
    )

    bal = _fresh()
    validate_annual_vacation_request(
        date_from=date(2026, 5, 1),
        date_to=date(2026, 5, 2),
        days_count=2,
        balances_by_year={2026: bal},
    )
    after = simulate_balance_after_consume(bal, days=2, as_approved=True)
    assert after.flexible_days_used == 2
    assert after.flexible_days_remaining == 5
    assert after.continuous_14_satisfied is False

    validate_annual_vacation_request(
        date_from=date(2026, 5, 10),
        date_to=date(2026, 5, 12),
        days_count=3,
        balances_by_year={2026: after},
    )


def test_after_seven_flexible_short_rejected_suggests_14_or_dayoff():
    ensure_service_in_path("vacation")
    from application.vacation_balance import validate_annual_vacation_request

    bal = _fresh(flex_used=7)
    with pytest.raises(ValueError, match="14.*Day Off|Day Off"):
        validate_annual_vacation_request(
            date_from=date(2026, 6, 1),
            date_to=date(2026, 6, 1),
            days_count=1,
            balances_by_year={2026: bal},
        )


def test_fourteen_days_ok_after_flexible_exhausted():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        count_calendar_days_inclusive,
        simulate_balance_after_consume,
        validate_annual_vacation_request,
    )

    bal = _fresh(flex_used=7)
    d0, d1 = date(2026, 6, 1), date(2026, 6, 14)
    days = count_calendar_days_inclusive(d0, d1)
    validate_annual_vacation_request(
        date_from=d0,
        date_to=d1,
        days_count=days,
        balances_by_year={2026: bal},
    )
    approved = simulate_balance_after_consume(bal, days=days, as_approved=True)
    assert approved.continuous_14_satisfied is True


def test_three_weeks_is_paid_annual_not_unpaid_when_within_entitlement():
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
        flexible_days_max=7,
        flexible_days_used=7,
        flexible_days_remaining=7,
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
        flexible_days_max=7,
        flexible_days_used=0,
        flexible_days_remaining=7,
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


def test_pending_reserves_flexible_pool():
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance, validate_annual_vacation_request

    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=0,
        pending_days=5,
        remaining_days=23,
        continuous_14_satisfied=False,
        min_continuous_days=14,
        flexible_days_max=7,
        flexible_days_used=5,
        flexible_days_remaining=2,
    )
    validate_annual_vacation_request(
        date_from=date(2026, 10, 1),
        date_to=date(2026, 10, 2),
        days_count=2,
        balances_by_year={2026: bal},
    )
    with pytest.raises(ValueError, match="Day Off"):
        validate_annual_vacation_request(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 3),
            days_count=3,
            balances_by_year={2026: bal},
        )


def test_year_split_counts_each_year_against_own_balance():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        VacationBalance,
        days_of_period_in_year,
        validate_annual_vacation_request,
    )

    bal_2025 = VacationBalance(
        year=2025,
        employee_user_id=1,
        entitled_days=28,
        used_days=26,
        pending_days=0,
        remaining_days=2,
        continuous_14_satisfied=True,
        min_continuous_days=14,
        flexible_days_max=7,
        flexible_days_used=0,
        flexible_days_remaining=7,
    )
    bal_2026 = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=0,
        pending_days=0,
        remaining_days=28,
        continuous_14_satisfied=False,
        min_continuous_days=14,
        flexible_days_max=7,
        flexible_days_used=0,
        flexible_days_remaining=7,
    )
    d0, d1 = date(2025, 12, 30), date(2026, 1, 5)
    assert days_of_period_in_year(d0, d1, 2025) == 2
    assert days_of_period_in_year(d0, d1, 2026) == 5
    # 5 short days in 2026 ok within flexible 7; 2 in 2025 within remaining
    validate_annual_vacation_request(
        date_from=d0,
        date_to=d1,
        days_count=7,
        balances_by_year={2025: bal_2025, 2026: bal_2026},
    )
