"""Annual vacation balance and legislation rules (calendar days).

Default entitlement: 28 calendar days/year (configurable).
Mandatory continuous portion: first annual leave block must be >= 14 days
until such a continuous portion has been used (approved); afterwards any
size up to remaining balance is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.kind_legend import KIND_BY_KEY
from infrastructure.config import get_settings
from infrastructure.models import (
    LEAVE_STATUS_APPROVED,
    LEAVE_STATUS_PENDING,
    AbsenceDay,
    LeaveRequest,
    ScheduleEmployee,
)

ANNUAL_VACATION_KIND = KIND_BY_KEY["annual_vacation"]  # 1


def count_calendar_days_inclusive(d_from: date, d_to: date) -> int:
    if d_to < d_from:
        return 0
    return (d_to - d_from).days + 1


def days_of_period_in_year(d_from: date, d_to: date, year: int) -> int:
    """How many inclusive calendar days of [d_from, d_to] fall into `year`."""
    if d_to < d_from:
        return 0
    y_start = date(year, 1, 1)
    y_end = date(year, 12, 31)
    start = max(d_from, y_start)
    end = min(d_to, y_end)
    if end < start:
        return 0
    return count_calendar_days_inclusive(start, end)


@dataclass(frozen=True)
class VacationBalance:
    year: int
    employee_user_id: int
    entitled_days: int
    used_days: int
    pending_days: int
    remaining_days: int
    continuous_14_satisfied: bool
    min_continuous_days: int

    def as_dict(self) -> dict:
        return {
            "year": self.year,
            "employeeUserId": self.employee_user_id,
            "entitledDays": self.entitled_days,
            "usedDays": self.used_days,
            "pendingDays": self.pending_days,
            "remainingDays": self.remaining_days,
            "continuous14Satisfied": self.continuous_14_satisfied,
            "minContinuousDays": self.min_continuous_days,
        }


async def _sum_leave_request_days(
    session: AsyncSession,
    *,
    employee_user_id: int,
    year: int,
    statuses: tuple[str, ...],
) -> int:
    r = await session.execute(
        select(LeaveRequest).where(
            LeaveRequest.employee_user_id == employee_user_id,
            LeaveRequest.kind_code == ANNUAL_VACATION_KIND,
            LeaveRequest.status.in_(statuses),
            LeaveRequest.date_from <= date(year, 12, 31),
            LeaveRequest.date_to >= date(year, 1, 1),
        )
    )
    total = 0
    for req in r.scalars().all():
        total += days_of_period_in_year(req.date_from, req.date_to, year)
    return total


async def _manual_annual_days_in_year(
    session: AsyncSession,
    *,
    employee_user_id: int,
    year: int,
) -> int:
    """Count annual-vacation schedule days not linked to a leave request."""
    r = await session.execute(
        select(AbsenceDay)
        .join(ScheduleEmployee, AbsenceDay.employee_id == ScheduleEmployee.id)
        .where(
            ScheduleEmployee.auth_user_id == employee_user_id,
            ScheduleEmployee.year == year,
            AbsenceDay.kind_code == ANNUAL_VACATION_KIND,
            AbsenceDay.leave_request_id.is_(None),
            AbsenceDay.absence_on >= date(year, 1, 1),
            AbsenceDay.absence_on <= date(year, 12, 31),
        )
    )
    return len(list(r.scalars().all()))


async def _has_continuous_14_satisfied(
    session: AsyncSession,
    *,
    employee_user_id: int,
    year: int,
    min_continuous: int,
) -> bool:
    # Approved leave request of sufficient length overlapping the year.
    r = await session.execute(
        select(LeaveRequest).where(
            LeaveRequest.employee_user_id == employee_user_id,
            LeaveRequest.kind_code == ANNUAL_VACATION_KIND,
            LeaveRequest.status == LEAVE_STATUS_APPROVED,
            LeaveRequest.days_count >= min_continuous,
            LeaveRequest.date_from <= date(year, 12, 31),
            LeaveRequest.date_to >= date(year, 1, 1),
        )
    )
    if r.scalars().first() is not None:
        return True

    # Continuous stretch of schedule annual days (incl. manual) in the year.
    days_r = await session.execute(
        select(AbsenceDay.absence_on)
        .join(ScheduleEmployee, AbsenceDay.employee_id == ScheduleEmployee.id)
        .where(
            ScheduleEmployee.auth_user_id == employee_user_id,
            ScheduleEmployee.year == year,
            AbsenceDay.kind_code == ANNUAL_VACATION_KIND,
            AbsenceDay.absence_on >= date(year, 1, 1),
            AbsenceDay.absence_on <= date(year, 12, 31),
        )
        .order_by(AbsenceDay.absence_on)
    )
    dates = sorted({row[0] for row in days_r.all()})
    if not dates:
        return False
    run = 1
    best = 1
    for i in range(1, len(dates)):
        if dates[i] == dates[i - 1] + timedelta(days=1):
            run += 1
            best = max(best, run)
        else:
            run = 1
    return best >= min_continuous


async def get_vacation_balance(
    session: AsyncSession,
    *,
    employee_user_id: int,
    year: int,
) -> VacationBalance:
    settings = get_settings()
    entitled = max(0, int(settings.annual_entitled_days))
    min_cont = max(1, int(settings.min_continuous_vacation_days))

    used_from_requests = await _sum_leave_request_days(
        session,
        employee_user_id=employee_user_id,
        year=year,
        statuses=(LEAVE_STATUS_APPROVED,),
    )
    used_manual = await _manual_annual_days_in_year(
        session,
        employee_user_id=employee_user_id,
        year=year,
    )
    used = used_from_requests + used_manual

    pending = await _sum_leave_request_days(
        session,
        employee_user_id=employee_user_id,
        year=year,
        statuses=(LEAVE_STATUS_PENDING,),
    )

    remaining = max(0, entitled - used - pending)
    continuous_ok = await _has_continuous_14_satisfied(
        session,
        employee_user_id=employee_user_id,
        year=year,
        min_continuous=min_cont,
    )
    return VacationBalance(
        year=year,
        employee_user_id=employee_user_id,
        entitled_days=entitled,
        used_days=used,
        pending_days=pending,
        remaining_days=remaining,
        continuous_14_satisfied=continuous_ok,
        min_continuous_days=min_cont,
    )


def validate_annual_vacation_request(
    *,
    date_from: date,
    date_to: date,
    days_count: int,
    balances_by_year: dict[int, VacationBalance],
) -> None:
    """Raise ValueError with a clear Russian message if rules are violated."""
    if days_count < 1:
        raise ValueError("Укажите корректный период отпуска (не меньше 1 календарного дня).")

    years = range(date_from.year, date_to.year + 1)
    for year in years:
        bal = balances_by_year[year]
        days_in_year = days_of_period_in_year(date_from, date_to, year)
        if days_in_year <= 0:
            continue
        if days_in_year > bal.remaining_days:
            raise ValueError(
                f"Недостаточно дней отпуска на {year} год: доступно {bal.remaining_days} "
                f"(положено {bal.entitled_days}, использовано {bal.used_days}"
                + (f", в ожидании согласования {bal.pending_days}" if bal.pending_days else "")
                + f"), в заявке на этот год — {days_in_year}."
            )

    # Continuous portion rule applies to the whole continuous request period.
    primary_year = date_from.year
    bal0 = balances_by_year[primary_year]
    if not bal0.continuous_14_satisfied and days_count < bal0.min_continuous_days:
        raise ValueError(
            f"Пока не использована обязательная непрерывная часть отпуска "
            f"({bal0.min_continuous_days} календарных дней), оформить можно только отпуск "
            f"продолжительностью не менее {bal0.min_continuous_days} календарных дней. "
            f"В заявке — {days_count}."
        )
