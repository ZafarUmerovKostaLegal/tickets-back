"""Unit tests for annual vacation balance: flexible 7 + continuous 14."""

from datetime import date

import pytest

from support.service_path import ensure_service_in_path


def _bal(
    *,
    used: int = 0,
    pending: int = 0,
    remaining: int | None = None,
    continuous: bool = False,
    entitled: int = 28,
    flex_used: int = 0,
    flex_max: int = 7,
) -> object:
    ensure_service_in_path("vacation")
    from application.vacation_balance import VacationBalance

    rem = remaining if remaining is not None else max(0, entitled - used - pending)
    flex_rem = flex_max if continuous else max(0, flex_max - flex_used)
    return VacationBalance(
        year=2026,
        employee_user_id=1,
        entitled_days=entitled,
        used_days=used,
        pending_days=pending,
        remaining_days=rem,
        continuous_14_satisfied=continuous,
        min_continuous_days=14,
        flexible_days_max=flex_max,
        flexible_days_used=flex_used,
        flexible_days_remaining=flex_rem,
    )


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


def test_validate_allows_short_within_flexible_7():
    ensure_service_in_path("vacation")
    from application.vacation_balance import validate_annual_vacation_request

    bal = _bal()
    validate_annual_vacation_request(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 3),
        days_count=3,
        balances_by_year={2026: bal},
    )


def test_validate_rejects_short_when_flexible_exhausted():
    ensure_service_in_path("vacation")
    from application.vacation_balance import validate_annual_vacation_request

    bal = _bal(used=7, flex_used=7, remaining=21)
    with pytest.raises(ValueError, match="Day Off"):
        validate_annual_vacation_request(
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 2),
            days_count=2,
            balances_by_year={2026: bal},
        )


def test_validate_allows_14_when_flexible_exhausted():
    ensure_service_in_path("vacation")
    from application.vacation_balance import validate_annual_vacation_request

    bal = _bal(used=7, flex_used=7, remaining=21)
    validate_annual_vacation_request(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 14),
        days_count=14,
        balances_by_year={2026: bal},
    )


def test_validate_allows_14_when_continuous_not_yet_used():
    ensure_service_in_path("vacation")
    from application.vacation_balance import validate_annual_vacation_request

    bal = _bal()
    validate_annual_vacation_request(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 14),
        days_count=14,
        balances_by_year={2026: bal},
    )


def test_validate_allows_short_after_continuous_14():
    ensure_service_in_path("vacation")
    from application.vacation_balance import validate_annual_vacation_request

    bal = _bal(used=14, remaining=14, continuous=True)
    validate_annual_vacation_request(
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 3),
        days_count=3,
        balances_by_year={2026: bal},
    )


def test_validate_rejects_over_remaining():
    ensure_service_in_path("vacation")
    from application.vacation_balance import validate_annual_vacation_request

    bal = _bal(used=25, remaining=3, continuous=True)
    with pytest.raises(ValueError, match="Недостаточно дней"):
        validate_annual_vacation_request(
            date_from=date(2026, 10, 1),
            date_to=date(2026, 10, 5),
            days_count=5,
            balances_by_year={2026: bal},
        )
