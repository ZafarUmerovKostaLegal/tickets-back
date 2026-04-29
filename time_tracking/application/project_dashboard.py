

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from application.entry_pricing import (
    _billable_amount_for_entry,
    _cost_amount_for_entry,
)
from application.report_builder import (
    _fetch_expense_report_data,
    _invoice_info_for_time_entries,
    _load_projects_map,
    _load_user_cost_rates,
    _load_user_rates,
    filter_expense_rows_to_tt_projects,
)
from application.services.reports._base import _d, _money
from application.budget_mode import budget_limit_hours, budget_limit_money, budget_mode
from application.services.reports.budget_report_service import (
    _spent_hours_project,
    _spent_money_project,
)
from infrastructure.repositories import (
    ClientProjectRepository,
    ClientRepository,
    TimeEntryRepository,
    TimeTrackingUserRepository,
)
from infrastructure.repository_invoices import InvoiceRepository

_ZERO = Decimal(0)


def _hours_json(d: Decimal) -> float:

    return float(d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _week_start_monday(d: date) -> date:

    return d - timedelta(days=d.weekday())


async def build_client_project_dashboard(
    session: AsyncSession,
    *,
    client_id: str,
    project_id: str,
    date_from: date | None,
    date_to: date | None,
) -> dict | None:
    cpr = ClientProjectRepository(session)
    proj_row = await cpr.get_by_id(client_id, project_id)
    if not proj_row:
        return None
    project_currency = (getattr(proj_row, "currency", None) or "USD").strip()[:10] or "USD"
    if date_from is not None and date_to is not None and date_to < date_from:
        raise ValueError("Параметр date_to не может быть раньше date_from")

    cr = ClientRepository(session)
    client_row = await cr.get_by_id(client_id)

    entry_repo = TimeEntryRepository(session)
    tot, bill, nonb = await entry_repo.aggregate_totals_for_project(project_id, date_from, date_to)
    weeks = await entry_repo.aggregate_hours_by_week_for_project(project_id, date_from, date_to)
    by_user = await entry_repo.aggregate_by_user_for_project(date_from, date_to, project_id)

    entries = await entry_repo.list_entries_for_project(project_id, date_from, date_to)
    entry_ids = [e.id for e in entries]
    inv_map = await _invoice_info_for_time_entries(session, entry_ids) if entry_ids else {}

    uids = sorted({e.auth_user_id for e in entries})
    rates_map = await _load_user_rates(session, uids or None)
    cost_rates_map = await _load_user_cost_rates(session, uids or None)

    total_bill = Decimal(0)
    total_cost = Decimal(0)
    cost_any_incomplete = False
    unbilled_bill = Decimal(0)
    week_bill: dict[date, Decimal] = defaultdict(Decimal)
    task_money: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"billable": Decimal(0), "cost": Decimal(0)},
    )
    user_bill: defaultdict[int, Decimal] = defaultdict(lambda: Decimal(0))
    user_cost: defaultdict[int, Decimal] = defaultdict(lambda: Decimal(0))

    for e in entries:

        h = _d(e.hours)
        uid = e.auth_user_id
        tid = (
            str(e.task_id)
            if e.task_id
            else ("__unassigned_billable__" if e.is_billable else "__unassigned_non_billable__")
        )

        if e.is_billable:
            amt, _cur = _billable_amount_for_entry(
                h,
                e.is_billable,
                e.work_date,
                rates_map.get(uid),
                project_currency=project_currency,
                time_entry_project_id=project_id,
            )
            total_bill += amt
            user_bill[uid] += amt
            ws = _week_start_monday(e.work_date)
            week_bill[ws] += amt
            task_money[tid]["billable"] += amt
            if e.id not in inv_map:
                unbilled_bill += amt

        c_amt, c_rate, _c_cur = _cost_amount_for_entry(
            h, e.work_date, cost_rates_map.get(uid), project_currency=project_currency,
        )
        total_cost += c_amt
        user_cost[uid] += c_amt
        if h > 0 and c_rate is None:
            cost_any_incomplete = True

        task_money[tid]["cost"] += c_amt

    user_repo = TimeTrackingUserRepository(session)
    by_auth = {u.auth_user_id: u for u in await user_repo.list_users()}

    def _member_sort_key(item: tuple[int, tuple[Decimal, Decimal, Decimal]]) -> str:
        uid, _ = item
        u = by_auth.get(uid)
        return (u.display_name or u.email or str(uid)).lower() if u else str(uid).lower()

    team: list[dict] = []
    for uid, (ut, ub, un) in sorted(by_user.items(), key=_member_sort_key):
        if ut <= 0:
            continue
        u = by_auth.get(uid)
        label = (u.display_name or u.email or str(uid)) if u else str(uid)
        team.append(
            {
                "user_id": str(uid),
                "name": label,
                "hours": _hours_json(ut),
                "billable_hours": _hours_json(ub),
                "non_billable_hours": _hours_json(un),
                "billable_amount": float(_money(user_bill[uid])),
                "internal_cost_amount": float(_money(user_cost[uid])),
            }
        )

    hours_by_week = [
        {
            "week_start": wk.isoformat(),
            "hours": _hours_json(t),
            "billable_hours": _hours_json(b),
            "non_billable_hours": _hours_json(n),
        }
        for wk, t, b, n in weeks
    ]

    progress_by_week: list[dict] = []
    cum = Decimal(0)
    for wk in sorted(week_bill.keys()):
        cum += week_bill[wk]
        progress_by_week.append(
            {
                "week_start": wk.isoformat(),
                "cumulative_billable_amount": float(_money(cum)),
            }
        )

    task_rows: list[dict] = []
    for tid, tname, billable_default, hrs in await entry_repo.aggregate_task_hours_for_project(
        project_id, date_from, date_to
    ):
        if hrs <= 0:
            continue
        tm = task_money.get(str(tid), {"billable": Decimal(0), "cost": Decimal(0)})
        task_rows.append(
            {
                "task_id": tid,
                "name": tname,
                "billable": billable_default,
                "hours": _hours_json(hrs),
                "billable_amount": float(_money(tm["billable"])),
                "internal_cost_amount": float(_money(tm["cost"])),
            }
        )
    for is_b, hrs in await entry_repo.aggregate_unassigned_hours_by_billable_for_project(
        project_id, date_from, date_to
    ):
        if hrs <= 0:
            continue
        synthetic = "__unassigned_billable__" if is_b else "__unassigned_non_billable__"
        label = "Без задачи (оплачиваемые)" if is_b else "Без задачи (неоплачиваемые)"
        tm = task_money.get(synthetic, {"billable": Decimal(0), "cost": Decimal(0)})
        task_rows.append(
            {
                "task_id": synthetic,
                "name": label,
                "billable": is_b,
                "hours": _hours_json(hrs),
                "billable_amount": float(_money(tm["billable"])),
                "internal_cost_amount": float(_money(tm["cost"])),
            }
        )

    df_eff = date_from or date(2000, 1, 1)
    dt_eff = date_to or date.today()
    raw_exp = await _fetch_expense_report_data(df_eff, dt_eff, None, [project_id])
    _pmap = await _load_projects_map(session)
    raw_exp = filter_expense_rows_to_tt_projects(raw_exp, _pmap)
    exp_uzs = Decimal(0)
    exp_n = 0
    for row in raw_exp:
        if str(row.get("project_id") or "") != str(project_id):
            continue
        exp_uzs += _d(row.get("amount_uzs", 0) or 0)
        exp_n += 1

    inv_repo = InvoiceRepository(session)
    inv_models = await inv_repo.list_invoices(
        client_id=client_id,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        limit=100,
    )
    invoices_out: list[dict] = []
    for inv in inv_models:
        if inv.status == "canceled":
            continue
        invoices_out.append(
            {
                "id": inv.id,
                "issued_at": inv.issue_date.isoformat(),
                "amount": float(_money(inv.total_amount)),
                "currency": (inv.currency or "USD").strip() or "USD",
                "status": inv.status,
            }
        )


    hours_map = {project_id: tot}
    money_map = {project_id: total_bill}
    b_mode = budget_mode(proj_row)
    lim_h = budget_limit_hours(proj_row)
    lim_m = budget_limit_money(proj_row)
    spent_h = _spent_hours_project(proj_row, hours_map)
    spent_m = _spent_money_project(proj_row, money_map)
    rem_h = max(_ZERO, lim_h - spent_h) if lim_h > _ZERO else _ZERO
    rem_m = max(_ZERO, lim_m - spent_m) if lim_m > _ZERO else _ZERO

    def _pct(used: Decimal, limit: Decimal) -> float | None:
        if limit <= _ZERO:
            return None
        return float(
            min(
                Decimal("100"),
                (used / limit * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            )
        )

    budget_out: dict[str, Any] = {
        "hasBudget": (lim_h > _ZERO) or (lim_m > _ZERO),
        "budgetBy": b_mode,
        "currency": project_currency,
    }

    if b_mode == "none":
        budget_out["percentUsed"] = None
        budget_out["budget"] = 0.0
        budget_out["spent"] = 0.0
        budget_out["remaining"] = 0.0
    elif b_mode == "hours":
        budget_out["percentUsed"] = _pct(spent_h, lim_h)
        budget_out["budget"] = _hours_json(lim_h)
        budget_out["spent"] = _hours_json(spent_h)
        budget_out["remaining"] = _hours_json(rem_h)
    elif b_mode == "money":
        budget_out["percentUsed"] = _pct(spent_m, lim_m)
        budget_out["budget"] = float(_money(lim_m))
        budget_out["spent"] = float(_money(spent_m))
        budget_out["remaining"] = float(_money(rem_m))
    else:
        budget_out["percentUsedHours"] = _pct(spent_h, lim_h)
        budget_out["percentUsedMoney"] = _pct(spent_m, lim_m)
        _pu_vals = [
            x for x in (budget_out["percentUsedHours"], budget_out["percentUsedMoney"]) if x is not None
        ]
        budget_out["percentUsed"] = max(_pu_vals) if _pu_vals else None
        budget_out["budgetHours"] = {
            "limit": _hours_json(lim_h),
            "spent": _hours_json(spent_h),
            "remaining": _hours_json(rem_h),
        }
        budget_out["budgetMoney"] = {
            "limit": float(_money(lim_m)),
            "spent": float(_money(spent_m)),
            "remaining": float(_money(rem_m)),
        }

    return {
        "currency": project_currency,
        "budget": budget_out,
        "totals": {
            "total_hours": _hours_json(tot),
            "billable_hours": _hours_json(bill),
            "non_billable_hours": _hours_json(nonb),
            "billable_amount": float(_money(total_bill)),
            "internal_cost_amount": float(_money(total_cost)),
            "internal_costs_complete": not cost_any_incomplete,
            "unbilled_amount": float(_money(unbilled_bill)),
            "expense_amount_uzs": float(_money(exp_uzs)),
            "expense_count": exp_n,
        },
        "progress_by_week": progress_by_week,
        "hours_by_week": hours_by_week,
        "tasks": task_rows,
        "team": team,
        "invoices": invoices_out,
    }
