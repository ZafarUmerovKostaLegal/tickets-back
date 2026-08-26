"""Annual vacation balance and legislation rules (calendar days).

Default entitlement: 28 calendar days/year (configurable).

Rules for annual paid leave:
- Up to `flexible_annual_days` (default 7) may be taken in short parts
  (1–2–3… days) before the mandatory continuous block is used.
- Once the flexible pool is exhausted and the continuous block
  (>= min_continuous, default 14) is not yet used, further short annual
  requests are rejected — user should take >=14 continuous days or
  unpaid Day Off instead.
- After the continuous block is satisfied, any size up to remaining
  balance is allowed.
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
    LEAVE_STATUS_PENDING_FINAL,
    AbsenceDay,
    LeaveRequest,
    ScheduleEmployee,
)

ANNUAL_VACATION_KIND = KIND_BY_KEY["annual_vacation"]  # 1
# Обе ступени согласования держат дни в резерве до финального решения.
IN_PROGRESS_STATUSES = (LEAVE_STATUS_PENDING, LEAVE_STATUS_PENDING_FINAL)


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
    flexible_days_max: int
    flexible_days_used: int
    flexible_days_remaining: int

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
            "flexibleDaysMax": self.flexible_days_max,
            "flexibleDaysUsed": self.flexible_days_used,
            "flexibleDaysRemaining": self.flexible_days_remaining,
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


async def _sum_short_leave_request_days(
    session: AsyncSession,
    *,
    employee_user_id: int,
    year: int,
    min_continuous: int,
    statuses: tuple[str, ...],
) -> int:
    """Days from annual leave requests shorter than the continuous minimum."""
    r = await session.execute(
        select(LeaveRequest).where(
            LeaveRequest.employee_user_id == employee_user_id,
            LeaveRequest.kind_code == ANNUAL_VACATION_KIND,
            LeaveRequest.status.in_(statuses),
            LeaveRequest.days_count < min_continuous,
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
    flex_max = max(0, int(getattr(settings, "flexible_annual_days", 7) or 7))

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
        statuses=IN_PROGRESS_STATUSES,
    )

    remaining = max(0, entitled - used - pending)
    continuous_ok = await _has_continuous_14_satisfied(
        session,
        employee_user_id=employee_user_id,
        year=year,
        min_continuous=min_cont,
    )

    flex_used = await _sum_short_leave_request_days(
        session,
        employee_user_id=employee_user_id,
        year=year,
        min_continuous=min_cont,
        statuses=(LEAVE_STATUS_APPROVED, *IN_PROGRESS_STATUSES),
    )
    # Manual days (no leave request) count toward flexible pool until 14 is satisfied.
    if not continuous_ok:
        flex_used += used_manual
    flex_used = min(flex_used, flex_max) if flex_max else flex_used
    flex_remaining = max(0, flex_max - flex_used) if not continuous_ok else flex_max

    return VacationBalance(
        year=year,
        employee_user_id=employee_user_id,
        entitled_days=entitled,
        used_days=used,
        pending_days=pending,
        remaining_days=remaining,
        continuous_14_satisfied=continuous_ok,
        min_continuous_days=min_cont,
        flexible_days_max=flex_max,
        flexible_days_used=flex_used,
        flexible_days_remaining=flex_remaining,
    )


def validate_annual_vacation_request(
    *,
    date_from: date,
    date_to: date,
    days_count: int,
    balances_by_year: dict[int, VacationBalance],
) -> None:
    """Raise ValueError with a clear Russian message if rules are violated.

    Annual paid leave cannot exceed remaining entitlement. Days beyond the
    remaining balance are not auto-converted to unpaid leave — the request
    is rejected until the period is shortened.
    """
    if days_count < 1:
        raise ValueError("Укажите корректный период отпуска (не меньше 1 календарного дня).")

    years = range(date_from.year, date_to.year + 1)
    for year in years:
        bal = balances_by_year[year]
        days_in_year = days_of_period_in_year(date_from, date_to, year)
        if days_in_year <= 0:
            continue
        if days_in_year > bal.remaining_days:
            over = days_in_year - bal.remaining_days
            raise ValueError(
                f"Недостаточно дней оплачиваемого ежегодного отпуска на {year} год: "
                f"доступно {bal.remaining_days} "
                f"(положено {bal.entitled_days}, использовано {bal.used_days}"
                + (f", в ожидании согласования {bal.pending_days}" if bal.pending_days else "")
                + f"), в заявке на этот год — {days_in_year} "
                f"(сверх остатка {over} дн.). "
                "Дни сверх остатка не оформляются как ежегодный оплачиваемый отпуск "
                "и автоматически в неоплачиваемый не переводятся — сократите период."
            )

    primary_year = date_from.year
    bal0 = balances_by_year[primary_year]
    if bal0.continuous_14_satisfied:
        return

    # Continuous block not yet used.
    if days_count >= bal0.min_continuous_days:
        return

    # Short part: only within flexible pool.
    flex_rem = bal0.flexible_days_remaining
    if days_count <= flex_rem:
        return

    raise ValueError(
        f"Дробный ежегодный отпуск доступен только в пределах "
        f"{bal0.flexible_days_max} календарных дней "
        f"(уже использовано/зарезервировано {bal0.flexible_days_used}, "
        f"осталось {flex_rem}). "
        f"В заявке — {days_count} дн. "
        f"Дальше оформите непрерывный ежегодный отпуск не менее "
        f"{bal0.min_continuous_days} календарных дней "
        f"либо выберите Day Off (нерабочий / неоплачиваемый)."
    )


def simulate_balance_after_consume(
    bal: VacationBalance,
    *,
    days: int,
    as_approved: bool,
) -> VacationBalance:
    """Pure helper for tests / UI previews: apply a consume against a balance snapshot."""
    days = max(0, int(days))
    if as_approved:
        used = bal.used_days + days
        pending = bal.pending_days
        continuous = bal.continuous_14_satisfied or days >= bal.min_continuous_days
    else:
        used = bal.used_days
        pending = bal.pending_days + days
        continuous = bal.continuous_14_satisfied
    remaining = max(0, bal.entitled_days - used - pending)

    flex_used = bal.flexible_days_used
    if not continuous and days < bal.min_continuous_days:
        flex_used = min(bal.flexible_days_max, bal.flexible_days_used + days)
    flex_remaining = (
        max(0, bal.flexible_days_max - flex_used) if not continuous else bal.flexible_days_max
    )

    return VacationBalance(
        year=bal.year,
        employee_user_id=bal.employee_user_id,
        entitled_days=bal.entitled_days,
        used_days=used,
        pending_days=pending,
        remaining_days=remaining,
        continuous_14_satisfied=continuous,
        min_continuous_days=bal.min_continuous_days,
        flexible_days_max=bal.flexible_days_max,
        flexible_days_used=flex_used,
        flexible_days_remaining=flex_remaining,
    )
