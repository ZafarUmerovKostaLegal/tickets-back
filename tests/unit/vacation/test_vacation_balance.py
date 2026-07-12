"""Unit tests for annual vacation balance and 14-day continuous rule."""

from datetime import date

import pytest

from support.service_path import ensure_service_in_path


def test_count_and_split_days_by_year():
    ensure_service_in_path("vacation")
    from application.vacation_balance import (
        count_calendar_days_inclusive,
        days_of_period_in_year,
    )

    assert count_calendar_days_inclusive(date(2026, 7, 1), date(2026, 7, 14)) == 14
    assert count_calendar_days_inclusive(date(2026, 7, 14), date(2026, 7, 1)) == 0
    assert days_of_period_in_year(date(2025, 12, 28), date(2026, 1, 5), 2025) == 4
    assert days_of_period_in_year(date(2025, 12, 28), date(2026, 1, 5), 2026) == 5


def test_validate_rejects_short_before_continuous_14():
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance, validate_annual_vacation_request

    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=0,
        pending_days=0,
        remaining_days=28,
        continuous_14_satisfied=False,
        min_continuous_days=14,
    )
    with pytest.raises(ValueError, match="обязательная непрерывная часть"):
        validate_annual_vacation_request(
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 5),
            days_count=5,
            balances_by_year={2026: bal},
        )


def test_validate_allows_14_when_continuous_not_yet_used():
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance, validate_annual_vacation_request

    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=0,
        pending_days=0,
        remaining_days=28,
        continuous_14_satisfied=False,
        min_continuous_days=14,
    )
    validate_annual_vacation_request(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 14),
        days_count=14,
        balances_by_year={2026: bal},
    )


def test_validate_allows_short_after_continuous_14():
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance, validate_annual_vacation_request

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
    validate_annual_vacation_request(
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 2),
        days_count=2,
        balances_by_year={2026: bal},
    )


def test_validate_rejects_over_remaining():
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance, validate_annual_vacation_request

    bal = VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=28,
        used_days=20,
        pending_days=5,
        remaining_days=3,
        continuous_14_satisfied=True,
        min_continuous_days=14,
    )
    with pytest.raises(ValueError, match="Недостаточно дней оплачиваемого"):
        validate_annual_vacation_request(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 7),
            days_count=7,
            balances_by_year={2026: bal},
        )
