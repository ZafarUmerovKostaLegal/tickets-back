

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.report_builder import (
    _fetch_expense_report_data,
    _load_clients_map,
    _load_projects_map,
    _load_users_map,
    filter_expense_rows_to_tt_projects,
)
from infrastructure.models import TimeManagerClientExpenseCategoryModel
from application.services.reports._base import _d, _money, _ZERO, build_response

EXPENSE_GROUP_OPTIONS = frozenset({"clients", "projects", "categories", "team"})


async def _load_expense_categories_map(
    session: AsyncSession,
) -> dict[str, TimeManagerClientExpenseCategoryModel]:
    rows = (await session.execute(select(TimeManagerClientExpenseCategoryModel))).scalars().all()
    return {c.id: c for c in rows}


async def get_expense_report(
    session: AsyncSession,
    *,
    group_by: str,
    date_from: date,
    date_to: date,
    client_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    user_ids: list[int] | None = None,
    page: int = 1,
    per_page: int = 100,
) -> dict:
    raw_expenses = await _fetch_expense_report_data(
        date_from, date_to, user_ids, project_ids,
    )

    projects_map = await _load_projects_map(session)
    raw_expenses = filter_expense_rows_to_tt_projects(raw_expenses, projects_map)
    clients_map = await _load_clients_map(session)
    users_map = await _load_users_map(session)
    categories_map = await _load_expense_categories_map(session)

    if client_ids:
        client_ids_set = set(client_ids)
        raw_expenses = [
            e for e in raw_expenses
            if _get_client_id_for_expense(e, projects_map) in client_ids_set
        ]


    buckets: dict[Any, dict] = {}
    for e in raw_expenses:
        gid = _get_group_id(e, group_by, projects_map)
        bkt = buckets.setdefault(gid, {
            "total": _ZERO,
            "billable": _ZERO,
            "currency": "USD",
            "user_buckets": {},
        })
        amt = _d(e.get("equivalent_amount", 0) or e.get("amount_uzs", 0))
        bkt["total"] += amt
        if e.get("is_reimbursable"):
            bkt["billable"] += amt


        uid = e.get("created_by_user_id")
        if uid is not None:
            ubkt = bkt["user_buckets"].setdefault(uid, {"total": _ZERO, "billable": _ZERO})
            ubkt["total"] += amt
            if e.get("is_reimbursable"):
                ubkt["billable"] += amt

    all_rows: list[dict] = []
    for gid, bkt in buckets.items():
        row = _build_row(gid, bkt, group_by, users_map, projects_map, clients_map, categories_map)
        all_rows.append(row)

    all_rows.sort(key=lambda r: r.get("total_amount", 0), reverse=True)

    total_entries_count = len(all_rows)
    start = (page - 1) * per_page
    results = all_rows[start: start + per_page]

    return build_response(
        results=results,
        total_entries=total_entries_count,
        page=page,
        per_page=per_page,
        report_type="expenses",
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
    )


async def get_expense_report_all_rows(
    session: AsyncSession, **kwargs: Any
) -> list[dict]:
    kwargs["page"] = 1
    kwargs["per_page"] = 100_000
    result = await get_expense_report(session, **kwargs)
    return result.get("results", [])


def _get_client_id_for_expense(e: dict, projects_map: dict) -> str | None:
    p = projects_map.get(e.get("project_id")) if e.get("project_id") else None
    return p.client_id if p else None


def _get_group_id(e: dict, group_by: str, projects_map: dict) -> Any:
    if group_by == "projects":
        return e.get("project_id")
    elif group_by == "clients":
        return _get_client_id_for_expense(e, projects_map)
    elif group_by == "categories":
        return e.get("expense_category_id")
    else:
        return e.get("created_by_user_id")


def _build_users_list(user_buckets: dict, users_map: dict) -> list[dict[str, Any]]:
    result = []
    for uid, ubkt in user_buckets.items():
        u = users_map.get(uid)
        result.append({
            "user_id": uid,
            "user_name": (u.display_name or u.email) if u else str(uid or ""),
            "avatar_url": u.picture if u else None,
            "total_amount": _money(ubkt["total"]),
            "billable_amount": _money(ubkt["billable"]),
        })
    result.sort(key=lambda r: r["total_amount"], reverse=True)
    return result


def _build_row(
    gid: Any,
    bkt: dict,
    group_by: str,
    users_map: dict,
    projects_map: dict,
    clients_map: dict,
    categories_map: dict,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "total_amount": _money(bkt["total"]),
        "billable_amount": _money(bkt["billable"]),
        "currency": bkt["currency"],
    }

    if group_by == "clients":
        c = clients_map.get(gid) if gid else None
        row["client_id"] = gid
        row["client_name"] = c.name if c else None
        row["users"] = _build_users_list(bkt["user_buckets"], users_map)

    elif group_by == "projects":
        p = projects_map.get(gid) if gid else None
        c = clients_map.get(p.client_id) if (p and p.client_id) else None
        row["client_id"] = p.client_id if p else None
        row["client_name"] = c.name if c else None
        row["project_id"] = gid
        row["project_name"] = p.name if p else None
        row["users"] = _build_users_list(bkt["user_buckets"], users_map)

    elif group_by == "categories":
        cat = categories_map.get(gid) if gid else None
        row["expense_category_id"] = gid
        row["expense_category_name"] = cat.name if cat else None
        row["users"] = _build_users_list(bkt["user_buckets"], users_map)

    else:
        u = users_map.get(gid) if gid else None
        row["user_id"] = gid
        row["user_name"] = (u.display_name or u.email) if u else str(gid or "")
        row["avatar_url"] = u.picture if u else None
        row["is_contractor"] = False

    return row
