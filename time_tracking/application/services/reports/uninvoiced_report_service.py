

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.entry_pricing import _billable_amount_for_entry
from application.report_builder import (
    _base_entry_conditions,
    _load_clients_map,
    _load_projects_map,
    _load_user_rates,
    _load_users_map,
    _fetch_expense_report_data,
    filter_expense_rows_to_tt_projects,
)
from infrastructure.models import TimeEntryModel
from application.services.reports._base import _d, _hours, _money, _ZERO, build_response


async def get_uninvoiced_report(
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


    all_cond = _base_entry_conditions(
        date_from, date_to, user_ids, project_ids, client_ids, include_fixed_fee,
    )
    all_cond.append(TimeEntryModel.is_billable.is_(True))


    all_entries_q = select(
        TimeEntryModel.id,
        TimeEntryModel.auth_user_id,
        TimeEntryModel.project_id,
        TimeEntryModel.work_date,
        TimeEntryModel.hours.label("hours"),
    ).where(and_(*all_cond))
    all_entries = (await session.execute(all_entries_q)).all()


    uninv_cond = _base_entry_conditions(
        date_from, date_to, user_ids, project_ids, client_ids, include_fixed_fee,
        exclude_invoiced_time=True,
    )
    uninv_cond.append(TimeEntryModel.is_billable.is_(True))

    uninv_entries_q = select(TimeEntryModel).where(and_(*uninv_cond))
    uninv_entries = list((await session.execute(uninv_entries_q)).scalars().all())

    all_uid_set = {e.auth_user_id for e in all_entries} | {e.auth_user_id for e in uninv_entries}
    rates_map = await _load_user_rates(session, list(all_uid_set) or None)


    raw_expenses = await _fetch_expense_report_data(date_from, date_to, user_ids, project_ids)
    raw_expenses = filter_expense_rows_to_tt_projects(raw_expenses, projects_map)
    if client_ids:
        client_ids_set = set(client_ids)
        raw_expenses = [
            e for e in raw_expenses
            if _get_expense_client_id(e, projects_map) in client_ids_set
        ]


    total_by_project: dict[str | None, Decimal] = {}
    for e in all_entries:
        pid = e.project_id
        total_by_project[pid] = total_by_project.get(pid, _ZERO) + _d(e.hours)


    uninv_hours_by_project: dict[str | None, Decimal] = {}
    uninv_amount_by_project: dict[str | None, Decimal] = {}
    uninv_currency_by_project: dict[str | None, str] = {}

    user_buckets_by_project: dict[str | None, dict[int, dict]] = {}

    for e in uninv_entries:
        pid = e.project_id
        h = _d(e.hours)
        uninv_hours_by_project[pid] = uninv_hours_by_project.get(pid, _ZERO) + h
        p_ent = projects_map.get(pid) if pid else None
        pc = (getattr(p_ent, "currency", None) or "USD") if p_ent else "USD"
        amt, cur = _billable_amount_for_entry(
            h,
            e.is_billable,
            e.work_date,
            rates_map.get(e.auth_user_id),
            project_currency=pc,
            time_entry_project_id=pid,
        )
        uninv_amount_by_project[pid] = uninv_amount_by_project.get(pid, _ZERO) + amt
        if cur != "USD":
            uninv_currency_by_project[pid] = cur
        elif pid not in uninv_currency_by_project:
            uninv_currency_by_project[pid] = "USD"


        uid = e.auth_user_id
        if pid not in user_buckets_by_project:
            user_buckets_by_project[pid] = {}
        ubkt = user_buckets_by_project[pid].setdefault(uid, {
            "total": _ZERO, "billable": _ZERO, "amount": _ZERO, "currency": "USD",
        })
        ubkt["total"] += h
        ubkt["billable"] += h
        ubkt["amount"] += amt
        if cur != "USD":
            ubkt["currency"] = cur


    uninv_expenses_by_project: dict[str | None, Decimal] = {}
    for e in raw_expenses:
        pid = e.get("project_id")
        amt = _d(e.get("equivalent_amount", 0) or e.get("amount_uzs", 0))
        uninv_expenses_by_project[pid] = uninv_expenses_by_project.get(pid, _ZERO) + amt

    all_pids = set(total_by_project) | set(uninv_hours_by_project) | set(uninv_expenses_by_project)

    all_rows: list[dict] = []
    for pid in all_pids:
        p = projects_map.get(pid) if pid else None
        c = clients_map.get(p.client_id) if (p and p.client_id) else None
        total_h = total_by_project.get(pid, _ZERO)
        uninv_h = uninv_hours_by_project.get(pid, _ZERO)
        uninv_exp = uninv_expenses_by_project.get(pid, _ZERO)
        uninv_amt = uninv_amount_by_project.get(pid, _ZERO)

        project_currency = (getattr(p, "currency", None) or "USD") if p else "USD"
        currency = project_currency if project_currency != "USD" else uninv_currency_by_project.get(pid, "USD")


        user_buckets = user_buckets_by_project.get(pid, {})
        users_list = _build_users_list(user_buckets, users_map)

        all_rows.append({
            "client_id": p.client_id if p else None,
            "client_name": c.name if c else None,
            "project_id": pid,
            "project_name": p.name if p else None,
            "currency": currency,
            "total_hours": _hours(total_h),
            "uninvoiced_hours": _hours(uninv_h),
            "uninvoiced_expenses": _money(uninv_exp),
            "uninvoiced_amount": _money(uninv_amt),
            "users": users_list,
        })

    all_rows.sort(key=lambda r: r.get("uninvoiced_amount", 0), reverse=True)

    total_entries_count = len(all_rows)
    start = (page - 1) * per_page
    results = all_rows[start: start + per_page]

    return build_response(
        results=results,
        total_entries=total_entries_count,
        page=page,
        per_page=per_page,
        report_type="uninvoiced",
        group_by=None,
        date_from=date_from,
        date_to=date_to,
    )


async def get_uninvoiced_report_all_rows(
    session: AsyncSession, **kwargs: Any
) -> list[dict]:
    kwargs["page"] = 1
    kwargs["per_page"] = 100_000
    result = await get_uninvoiced_report(session, **kwargs)
    return result.get("results", [])


def _build_users_list(user_buckets: dict, users_map: dict) -> list[dict[str, Any]]:
    result = []
    for uid, ubkt in user_buckets.items():
        u = users_map.get(uid)
        result.append({
            "user_id": uid,
            "user_name": (u.display_name or u.email) if u else str(uid or ""),
            "avatar_url": u.picture if u else None,
            "uninvoiced_hours": _hours(ubkt["billable"]),
            "uninvoiced_amount": _money(ubkt["amount"]),
            "currency": ubkt["currency"],
        })
    result.sort(key=lambda r: r["uninvoiced_hours"], reverse=True)
    return result


def _get_expense_client_id(e: dict, projects_map: dict) -> str | None:
    p = projects_map.get(e.get("project_id")) if e.get("project_id") else None
    return p.client_id if p else None
