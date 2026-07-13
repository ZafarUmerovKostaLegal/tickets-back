

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.budget_mode import budget_limit_hours, budget_limit_money, budget_mode
from application.entry_pricing import billable_amount_respecting_package
from application.package_billing import (
    build_package_splits_index,
    is_hour_package_project,
    package_fee_x,
    package_hours_n,
)
from application.report_builder import (
    _base_entry_conditions,
    _load_clients_map,
    _load_initials_map,
    _load_projects_map,
    _load_tasks_map,
    _load_user_rates,
    _load_users_map,
)
from application.user_initials import resolve_user_initials
from infrastructure.models import TimeEntryModel, TimeManagerClientProjectModel
from application.services.reports._base import _d, _hours, _money, _ZERO, build_response


def _progress_percent(spent: Decimal, limit: Decimal) -> float:
    if limit <= _ZERO:
        return 0.0
    return round(float((spent / limit) * Decimal(100)), 2)


def _attach_budget_compat_fields(
    row: dict[str, Any],
    *,
    budget_value: float | None,
    spent_value: float | None,
    remaining_value: float | None,
    progress_percent: float,
) -> None:
    row["budget_amount"] = budget_value
    row["budget_spent_amount"] = spent_value
    row["budget_remaining_amount"] = remaining_value
    row["progress_percent"] = progress_percent

                                                  
    row["budgetAmount"] = budget_value
    row["budgetSpent"] = spent_value
    row["budgetRemaining"] = remaining_value
    row["progressPercent"] = progress_percent


def _spent_hours_project(
    p: TimeManagerClientProjectModel,
    hours_by_project: dict[str, Decimal],
) -> Decimal:
    return hours_by_project.get(p.id, _ZERO)


def _spent_money_project(
    p: TimeManagerClientProjectModel,
    amount_by_project: dict[str, Decimal],
) -> Decimal:
    return amount_by_project.get(p.id, _ZERO)


async def get_budget_report(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    client_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    user_ids: list[int] | None = None,
    include_fixed_fee: bool = True,
    page: int = 1,
    per_page: int = 100,
) -> dict:
    projects_map = await _load_projects_map(session)
    clients_map = await _load_clients_map(session)
    users_map = await _load_users_map(session)
    initials_map = await _load_initials_map(session)


    target_projects = list(projects_map.values())
    if project_ids:
        pids_set = set(project_ids)
        target_projects = [p for p in target_projects if p.id in pids_set]
    if client_ids:
        cids_set = set(client_ids)
        target_projects = [p for p in target_projects if p.client_id in cids_set]
    if not include_fixed_fee:
        target_projects = [p for p in target_projects if p.project_type != "fixed_fee"]

    target_pids = [p.id for p in target_projects]
    if not target_pids:
        return build_response(
            results=[],
            total_entries=0,
            page=page,
            per_page=per_page,
            report_type="project-budget",
            group_by=None,
            date_from=date_from,
            date_to=date_to,
        )


    cond = _base_entry_conditions(
        date_from, date_to, user_ids, target_pids, client_ids, True,
    )
    entries_q = select(TimeEntryModel).where(and_(*cond))
    entries = list((await session.execute(entries_q)).scalars().all())

    all_user_ids = list({e.auth_user_id for e in entries})
    rates_map = await _load_user_rates(session, all_user_ids or None)
    tasks_map = await _load_tasks_map(session)

    package_splits, package_months_by_project = build_package_splits_index(
        projects_map,
        entries,
        date_from=date_from,
        date_to=date_to,
        tasks_map=tasks_map,
    )

    hours_by_project: dict[str, Decimal] = {}
    amount_by_project: dict[str, Decimal] = {}
    user_buckets_by_project: dict[str, dict[int, dict]] = {}

    for e in entries:
        pid = e.project_id
        if pid is None:
            continue
        h = _d(e.hours)
        hours_by_project[pid] = hours_by_project.get(pid, _ZERO) + h

        uid = e.auth_user_id
        if pid not in user_buckets_by_project:
            user_buckets_by_project[pid] = {}
        ubkt = user_buckets_by_project[pid].setdefault(uid, {"hours": _ZERO, "amount": _ZERO})
        ubkt["hours"] += h

        if e.is_billable:
            p_ent = projects_map.get(pid)
            pc = (getattr(p_ent, "currency", None) or "USD") if p_ent else "USD"
            amt, _ = billable_amount_respecting_package(
                h,
                e.is_billable,
                e.work_date,
                rates_map.get(uid),
                project_currency=pc,
                time_entry_project_id=pid,
                task=tasks_map.get(e.task_id) if e.task_id else None,
                package_split=package_splits.get(str(e.id)),
            )
            amount_by_project[pid] = amount_by_project.get(pid, _ZERO) + amt
            ubkt["amount"] += amt

    all_rows: list[dict] = []
    for p in target_projects:
        c = clients_map.get(p.client_id) if p.client_id else None
        mode = budget_mode(p)
        lim_h = budget_limit_hours(p)
        lim_m = budget_limit_money(p)
        spent_h = _spent_hours_project(p, hours_by_project)
        spent_m = _spent_money_project(p, amount_by_project)
        rem_h = max(_ZERO, lim_h - spent_h) if lim_h > _ZERO else _ZERO
        rem_m = max(_ZERO, lim_m - spent_m) if lim_m > _ZERO else _ZERO

        user_buckets = user_buckets_by_project.get(p.id, {})
        users_list = _build_users_list(user_buckets, users_map, initials_map)
        project_currency = (getattr(p, "currency", None) or "USD")

        row: dict[str, Any] = {
            "client_id": p.client_id,
            "client_name": c.name if c else None,
            "project_id": p.id,
            "project_name": p.name,
            "currency": project_currency,
            "budget_is_monthly": p.budget_resets_every_month,
            "budget_by": mode,
            "is_active": not p.is_archived,
            "users": users_list,
        }

        if is_hour_package_project(p):
            row["project_type"] = "hour_package"
            row["package_hours_per_month"] = float(package_hours_n(p))
            row["package_fee_amount"] = float(package_fee_x(p))
            months = package_months_by_project.get(p.id, [])
            row["package_months"] = [s.as_dict() for s in months]
            if months:
                last = months[-1]
                row["budget_by"] = "hour_package"
                row["has_budget"] = True
                row["budget"] = float(last.capacity)
                row["budget_spent"] = float(last.used_hours)
                row["budget_remaining"] = float(max(_ZERO, last.capacity - last.used_hours))
                _attach_budget_compat_fields(
                    row,
                    budget_value=row["budget"],
                    spent_value=row["budget_spent"],
                    remaining_value=row["budget_remaining"],
                    progress_percent=_progress_percent(last.used_hours, last.capacity),
                )
                all_rows.append(row)
                continue

        if mode == "none":
            row["has_budget"] = False
            row["budget"] = 0.0
            row["budget_spent"] = 0.0
            row["budget_remaining"] = 0.0
            _attach_budget_compat_fields(
                row,
                budget_value=0.0,
                spent_value=0.0,
                remaining_value=0.0,
                progress_percent=0.0,
            )
        elif mode == "hours":
            row["has_budget"] = lim_h > _ZERO
            row["budget"] = _hours(lim_h)
            row["budget_spent"] = _hours(spent_h)
            row["budget_remaining"] = _hours(rem_h)
            _attach_budget_compat_fields(
                row,
                budget_value=row["budget"],
                spent_value=row["budget_spent"],
                remaining_value=row["budget_remaining"],
                progress_percent=_progress_percent(spent_h, lim_h),
            )
        elif mode == "money":
            row["has_budget"] = lim_m > _ZERO
            row["budget"] = _money(lim_m)
            row["budget_spent"] = _money(spent_m)
            row["budget_remaining"] = _money(rem_m)
            _attach_budget_compat_fields(
                row,
                budget_value=row["budget"],
                spent_value=row["budget_spent"],
                remaining_value=row["budget_remaining"],
                progress_percent=_progress_percent(spent_m, lim_m),
            )
        else:
            row["has_budget"] = (lim_h > _ZERO) or (lim_m > _ZERO)
            row["budget"] = None
            row["budget_spent"] = None
            row["budget_remaining"] = None
            row["budget_hours_limit"] = _hours(lim_h)
            row["budget_hours_spent"] = _hours(spent_h)
            row["budget_hours_remaining"] = _hours(rem_h)
            row["budget_money_limit"] = _money(lim_m)
            row["budget_money_spent"] = _money(spent_m)
            row["budget_money_remaining"] = _money(rem_m)
            progress_h = _progress_percent(spent_h, lim_h) if lim_h > _ZERO else 0.0
            progress_m = _progress_percent(spent_m, lim_m) if lim_m > _ZERO else 0.0
            _attach_budget_compat_fields(
                row,
                budget_value=row["budget_money_limit"],
                spent_value=row["budget_money_spent"],
                remaining_value=row["budget_money_remaining"],
                progress_percent=max(progress_h, progress_m),
            )

        all_rows.append(row)

    all_rows.sort(key=lambda r: r.get("project_name") or "", reverse=False)

    total_entries_count = len(all_rows)
    start = (page - 1) * per_page
    results = all_rows[start: start + per_page]

    return build_response(
        results=results,
        total_entries=total_entries_count,
        page=page,
        per_page=per_page,
        report_type="project-budget",
        group_by=None,
        date_from=date_from,
        date_to=date_to,
    )


async def get_budget_report_all_rows(
    session: AsyncSession, **kwargs: Any
) -> list[dict]:
    kwargs["page"] = 1
    kwargs["per_page"] = 100_000
    result = await get_budget_report(session, **kwargs)
    return result.get("results", [])


def _build_users_list(
    user_buckets: dict,
    users_map: dict,
    initials_map: dict[int, str | None],
) -> list[dict[str, Any]]:

    result = []
    for uid, ubkt in user_buckets.items():
        u = users_map.get(uid)
        result.append({
            "user_id": uid,
            "user_name": (u.display_name or u.email) if u else str(uid or ""),
            "initials": resolve_user_initials(u, initials_map=initials_map),
            "avatar_url": u.picture if u else None,
            "hours_logged": _hours(ubkt["hours"]),
            "amount_logged": _money(ubkt["amount"]),
        })
    result.sort(key=lambda r: r["hours_logged"], reverse=True)
    return result
