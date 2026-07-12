"""Monthly hour package: N hours for $X, unused rolls over 1 month, overage at rates.

Consumption within a month: current allotment first, then carried-in hours.
Unused carried-in expires at month end; only unused current allotment carries out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Sequence

from application.entry_pricing import _billable_amount_for_entry
from application.services.reports._base import _ZERO, _d

PACKAGE_ROLLOVER_MAX_MONTHS = 1
HOUR_PACKAGE_PROJECT_TYPE = "hour_package"


def is_hour_package_project(project: Any) -> bool:
    return (getattr(project, "project_type", None) or "") == HOUR_PACKAGE_PROJECT_TYPE


def package_hours_n(project: Any) -> Decimal:
    v = getattr(project, "package_hours_per_month", None)
    if v is not None and _d(v) > 0:
        return _d(v)
    # Mirror budget_hours when package fields unset (legacy / display sync).
    bh = getattr(project, "budget_hours", None)
    if bh is not None and _d(bh) > 0:
        return _d(bh)
    return _ZERO


def package_fee_x(project: Any) -> Decimal:
    v = getattr(project, "package_fee_amount", None)
    if v is not None and _d(v) > 0:
        return _d(v)
    ba = getattr(project, "budget_amount", None)
    if ba is not None and _d(ba) > 0:
        return _d(ba)
    return _ZERO


def month_key(d: date) -> tuple[int, int]:
    return (d.year, d.month)


def add_month(y: int, m: int, delta: int = 1) -> tuple[int, int]:
    idx = y * 12 + (m - 1) + delta
    return (idx // 12, idx % 12 + 1)


def months_inclusive(start: date, end: date) -> list[tuple[int, int]]:
    if end < start:
        return []
    out: list[tuple[int, int]] = []
    y, m = start.year, start.month
    ey, em = end.year, end.month
    while (y, m) <= (ey, em):
        out.append((y, m))
        y, m = add_month(y, m, 1)
    return out


def month_label(y: int, m: int) -> str:
    return f"{y:04d}-{m:02d}"


@dataclass(frozen=True)
class MonthPackageSummary:
    year: int
    month: int
    package_hours: Decimal
    package_fee: Decimal
    carried_in: Decimal
    capacity: Decimal
    used_hours: Decimal
    used_from_current: Decimal
    used_from_rollover: Decimal
    covered_hours: Decimal
    overage_hours: Decimal
    expired_rollover: Decimal
    carry_out: Decimal
    overage_amount: Decimal = _ZERO

    @property
    def billable_total(self) -> Decimal:
        return _d(self.package_fee) + _d(self.overage_amount)

    def as_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "month": self.month,
            "monthLabel": month_label(self.year, self.month),
            "packageHours": float(self.package_hours),
            "packageFee": float(self.package_fee),
            "carriedIn": float(self.carried_in),
            "capacity": float(self.capacity),
            "usedHours": float(self.used_hours),
            "usedFromCurrent": float(self.used_from_current),
            "usedFromRollover": float(self.used_from_rollover),
            "coveredHours": float(self.covered_hours),
            "overageHours": float(self.overage_hours),
            "expiredRollover": float(self.expired_rollover),
            "carryOut": float(self.carry_out),
            "overageAmount": float(self.overage_amount),
            "billableTotal": float(self.billable_total),
        }


@dataclass(frozen=True)
class EntryPackageSplit:
    entry_id: str
    hours: Decimal
    covered_hours: Decimal
    overage_hours: Decimal

    def as_dict(self) -> dict[str, Any]:
        return {
            "entryId": self.entry_id,
            "hours": float(self.hours),
            "coveredHours": float(self.covered_hours),
            "overageHours": float(self.overage_hours),
        }


def summarize_month(
    *,
    year: int,
    month: int,
    package_hours: Decimal,
    package_fee: Decimal,
    carried_in: Decimal,
    used_hours: Decimal,
) -> MonthPackageSummary:
    n = max(_ZERO, _d(package_hours))
    fee = max(_ZERO, _d(package_fee))
    cin = max(_ZERO, _d(carried_in))
    used = max(_ZERO, _d(used_hours))
    capacity = n + cin
    used_from_current = min(used, n)
    rem = used - used_from_current
    used_from_rollover = min(rem, cin)
    covered = used_from_current + used_from_rollover
    overage = used - covered
    expired = cin - used_from_rollover
    carry_out = n - used_from_current
    return MonthPackageSummary(
        year=year,
        month=month,
        package_hours=n,
        package_fee=fee,
        carried_in=cin,
        capacity=capacity,
        used_hours=used,
        used_from_current=used_from_current,
        used_from_rollover=used_from_rollover,
        covered_hours=covered,
        overage_hours=overage,
        expired_rollover=expired,
        carry_out=carry_out,
    )


def walk_months(
    *,
    package_hours: Decimal,
    package_fee: Decimal,
    used_by_month: dict[tuple[int, int], Decimal],
    months: Sequence[tuple[int, int]],
    initial_carry_in: Decimal = _ZERO,
) -> list[MonthPackageSummary]:
    carried = max(_ZERO, _d(initial_carry_in))
    out: list[MonthPackageSummary] = []
    for y, m in months:
        used = _d(used_by_month.get((y, m), _ZERO))
        summary = summarize_month(
            year=y,
            month=m,
            package_hours=package_hours,
            package_fee=package_fee,
            carried_in=carried,
            used_hours=used,
        )
        out.append(summary)
        carried = summary.carry_out
    return out


def _entry_sort_key(e: Any) -> tuple:
    return (getattr(e, "work_date", date.min), str(getattr(e, "id", "")))


def attribute_entries_for_months(
    *,
    package_hours: Decimal,
    entries: Sequence[Any],
    months: Sequence[tuple[int, int]],
    initial_carry_in: Decimal = _ZERO,
) -> tuple[list[MonthPackageSummary], dict[str, EntryPackageSplit]]:
    """Attribute billable entries chronologically; current allotment first, then rollover."""
    n = max(_ZERO, _d(package_hours))
    by_month: dict[tuple[int, int], list[Any]] = {k: [] for k in months}
    used_by_month: dict[tuple[int, int], Decimal] = {k: _ZERO for k in months}
    for e in entries:
        if not getattr(e, "is_billable", True):
            continue
        if getattr(e, "voided_at", None) is not None:
            continue
        wd = getattr(e, "work_date", None)
        if wd is None:
            continue
        key = month_key(wd)
        if key not in by_month:
            continue
        h = _d(getattr(e, "hours", 0))
        if h <= 0:
            continue
        by_month[key].append(e)
        used_by_month[key] = used_by_month.get(key, _ZERO) + h

    fee_placeholder = _ZERO
    month_summaries = walk_months(
        package_hours=n,
        package_fee=fee_placeholder,
        used_by_month=used_by_month,
        months=months,
        initial_carry_in=initial_carry_in,
    )

    splits: dict[str, EntryPackageSplit] = {}
    carried = max(_ZERO, _d(initial_carry_in))
    for summary in month_summaries:
        key = (summary.year, summary.month)
        rem_current = n
        rem_rollover = carried
        for e in sorted(by_month.get(key, []), key=_entry_sort_key):
            h = _d(e.hours)
            from_current = min(h, rem_current)
            rem_current -= from_current
            rem_h = h - from_current
            from_rollover = min(rem_h, rem_rollover)
            rem_rollover -= from_rollover
            covered = from_current + from_rollover
            overage = h - covered
            splits[str(e.id)] = EntryPackageSplit(
                entry_id=str(e.id),
                hours=h,
                covered_hours=covered,
                overage_hours=overage,
            )
        carried = summary.carry_out
    return month_summaries, splits


def build_month_summaries_with_fees(
    *,
    project: Any,
    used_by_month: dict[tuple[int, int], Decimal],
    months: Sequence[tuple[int, int]],
    initial_carry_in: Decimal = _ZERO,
    overage_amount_by_month: dict[tuple[int, int], Decimal] | None = None,
) -> list[MonthPackageSummary]:
    n = package_hours_n(project)
    fee = package_fee_x(project)
    base = walk_months(
        package_hours=n,
        package_fee=fee,
        used_by_month=used_by_month,
        months=months,
        initial_carry_in=initial_carry_in,
    )
    if not overage_amount_by_month:
        return base
    out: list[MonthPackageSummary] = []
    for s in base:
        oa = _d(overage_amount_by_month.get((s.year, s.month), _ZERO))
        out.append(
            MonthPackageSummary(
                year=s.year,
                month=s.month,
                package_hours=s.package_hours,
                package_fee=s.package_fee,
                carried_in=s.carried_in,
                capacity=s.capacity,
                used_hours=s.used_hours,
                used_from_current=s.used_from_current,
                used_from_rollover=s.used_from_rollover,
                covered_hours=s.covered_hours,
                overage_hours=s.overage_hours,
                expired_rollover=s.expired_rollover,
                carry_out=s.carry_out,
                overage_amount=oa,
            )
        )
    return out


def overage_amount_for_split(
    split: EntryPackageSplit,
    *,
    is_billable: bool,
    work_date: date,
    user_rates: list[Any] | None,
    project_currency: str | None,
    time_entry_project_id: str | None,
) -> Decimal:
    if split.overage_hours <= 0 or not is_billable:
        return _ZERO
    amt, _ = _billable_amount_for_entry(
        split.overage_hours,
        True,
        work_date,
        user_rates,
        project_currency=project_currency,
        time_entry_project_id=time_entry_project_id,
    )
    return _d(amt)


def compute_entry_splits_for_project_entries(
    project: Any,
    entries: Sequence[Any],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[MonthPackageSummary], dict[str, EntryPackageSplit]]:
    """Full chain for a project: include prior month before date_from for carry-in."""
    if not is_hour_package_project(project):
        return [], {}
    n = package_hours_n(project)
    fee = package_fee_x(project)
    if n <= 0:
        return [], {}

    billable = [
        e
        for e in entries
        if getattr(e, "is_billable", True)
        and getattr(e, "voided_at", None) is None
        and _d(getattr(e, "hours", 0)) > 0
        and getattr(e, "work_date", None) is not None
    ]
    if not billable and date_from and date_to:
        months = months_inclusive(date_from, date_to)
        summaries = walk_months(
            package_hours=n,
            package_fee=fee,
            used_by_month={},
            months=months,
            initial_carry_in=_ZERO,
        )
        return summaries, {}

    if not billable:
        return [], {}

    dates = [e.work_date for e in billable]
    min_d = min(dates)
    max_d = max(dates)
    if date_from:
        min_d = min(min_d, date_from)
    if date_to:
        max_d = max(max_d, date_to)

    # Start one month earlier so carry-in into first visible month is correct.
    start_y, start_m = add_month(min_d.year, min_d.month, -1)
    chain_start = date(start_y, start_m, 1)
    months = months_inclusive(chain_start, max_d)

    summaries_raw, splits = attribute_entries_for_months(
        package_hours=n,
        entries=billable,
        months=months,
        initial_carry_in=_ZERO,
    )
    # Attach fees
    summaries = [
        MonthPackageSummary(
            year=s.year,
            month=s.month,
            package_hours=s.package_hours,
            package_fee=fee,
            carried_in=s.carried_in,
            capacity=s.capacity,
            used_hours=s.used_hours,
            used_from_current=s.used_from_current,
            used_from_rollover=s.used_from_rollover,
            covered_hours=s.covered_hours,
            overage_hours=s.overage_hours,
            expired_rollover=s.expired_rollover,
            carry_out=s.carry_out,
        )
        for s in summaries_raw
    ]

    if date_from or date_to:
        df = date_from or min_d
        dt = date_to or max_d
        visible = {(y, m) for y, m in months_inclusive(df, dt)}
        summaries = [s for s in summaries if (s.year, s.month) in visible]
        # Keep splits for all attributed entries (including prior month used only for carry).
    return summaries, splits


def package_fee_description(project_name: str, year: int, month: int, n: Decimal) -> str:
    return f"Package {float(n):g} h — {month_label(year, month)} ({project_name})"


def build_package_splits_index(
    projects_map: dict[str, Any],
    entries: Sequence[Any],
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[dict[str, EntryPackageSplit], dict[str, list[MonthPackageSummary]]]:
    """Entry-id → split and project-id → month summaries for all hour_package projects."""
    by_project: dict[str, list[Any]] = {}
    for e in entries:
        pid = (getattr(e, "project_id", None) or "").strip()
        if not pid:
            continue
        p = projects_map.get(pid)
        if not p or not is_hour_package_project(p):
            continue
        by_project.setdefault(pid, []).append(e)

    splits: dict[str, EntryPackageSplit] = {}
    months_by_project: dict[str, list[MonthPackageSummary]] = {}
    for pid, ents in by_project.items():
        # Load attribution needs all billable entries for carry — caller should pass full set
        # for these projects when possible; here we use what we have.
        summaries, sp = compute_entry_splits_for_project_entries(
            projects_map[pid],
            ents,
            date_from=date_from,
            date_to=date_to,
        )
        splits.update(sp)
        months_by_project[pid] = summaries
    return splits, months_by_project
