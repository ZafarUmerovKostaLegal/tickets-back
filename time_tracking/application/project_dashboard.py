

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from application.duplicate_time_entries import deduplicate_entries_for_report
from application.entry_pricing import (
    _cost_amount_for_entry,
    billable_amount_respecting_package,
)
from application.package_billing import (
    compute_entry_splits_for_project_entries,
    is_hour_package_project,
)
from application.report_builder import (
    _fetch_expense_report_data,
    _invoice_info_for_time_entries,
    _load_initials_map,
    _load_user_cost_rates,
    _load_user_rates,
)
from application.user_initials import resolve_user_initials
from application.services.reports._base import _d, _money
from application.budget_mode import budget_limit_hours, budget_limit_money, budget_mode
from application.services.reports.budget_report_service import (
    _spent_hours_project,
    _spent_money_project,
)
from application.time_rounding import hours_from_seconds
from infrastructure.repositories import (
    ClientProjectRepository,
    ClientRepository,
    ClientTaskRepository,
    TimeEntryRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)
from infrastructure.repository_invoices import InvoiceRepository

_ZERO = Decimal(0)


def _hours_json(d: Decimal) -> float:

    return float(d.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def _week_start_monday(d: date) -> date:

    return d - timedelta(days=d.weekday())


def _entry_hours(e: Any) -> Decimal:
    """Match time report: prefer stored hours, fallback to duration_seconds."""
    if getattr(e, "hours", None) is not None:
        return _d(e.hours)
    sec = getattr(e, "duration_seconds", None)
    if sec is not None:
        return hours_from_seconds(int(sec))
    return _ZERO


def _dedupe_dashboard_entries(
    entries: list[Any],
    *,
    proj_row: Any,
    rates_map: dict[int, list],
    tasks_map: dict[str, Any],
    package_splits: dict[str, Any] | None,
) -> list[Any]:
    """Same three-pass collapse as get_time_report for one project."""
    projects_map = {str(proj_row.id): proj_row}
    kept, _ = deduplicate_entries_for_report(
        entries,
        projects_map=projects_map,
        rates_map=rates_map,
        tasks_map=tasks_map,
    )
    if package_splits:
        kept, _ = deduplicate_entries_for_report(
            kept,
            projects_map=projects_map,
            rates_map=rates_map,
            tasks_map=tasks_map,
            package_splits=package_splits,
        )
    kept, _ = deduplicate_entries_for_report(
        kept,
        projects_map=projects_map,
        rates_map=rates_map,
        tasks_map=tasks_map,
        ignore_amount=True,
    )
    return kept


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
    access_repo = UserProjectAccessRepository(session)
    df_eff = date_from or date(2000, 1, 1)
    dt_eff = date_to or date.today()

    from_access = await access_repo.list_auth_user_ids_for_project(project_id)
    from_entries = await entry_repo.list_auth_users_with_entries_on_project(
        df_eff, dt_eff, project_id
    )

    entries = await entry_repo.list_entries_for_project(project_id, date_from, date_to)
    uids = sorted({e.auth_user_id for e in entries})
    rates_map = await _load_user_rates(session, uids or None)
    cost_rates_map = await _load_user_cost_rates(session, uids or None)
    task_repo = ClientTaskRepository(session)
    tasks_map = {str(t.id): t for t in await task_repo.list_for_project(project_id)}

    package_splits: dict[str, Any] = {}
    if is_hour_package_project(proj_row):
        # Package overage needs full project billable history for carry-in correctness.
        all_billable = [
            e
            for e in await entry_repo.list_entries_for_project(project_id, None, None)
            if e.is_billable
        ]
        _, package_splits = compute_entry_splits_for_project_entries(
            proj_row,
            all_billable,
            date_from=date_from,
            date_to=date_to,
            tasks_map=tasks_map,
        )

    # Align with time report: collapse near-duplicate entries before totals.
    entries = _dedupe_dashboard_entries(
        entries,
        proj_row=proj_row,
        rates_map=rates_map,
        tasks_map=tasks_map,
        package_splits=package_splits or None,
    )
    entry_ids = [e.id for e in entries]
    inv_map = await _invoice_info_for_time_entries(session, entry_ids) if entry_ids else {}

    tot = Decimal(0)
    bill = Decimal(0)
    nonb = Decimal(0)
    total_bill = Decimal(0)
    total_cost = Decimal(0)
    cost_any_incomplete = False
    unbilled_bill = Decimal(0)
    week_hours: dict[date, dict[str, Decimal]] = defaultdict(
        lambda: {"total": Decimal(0), "billable": Decimal(0), "non_billable": Decimal(0)}
    )
    week_bill: dict[date, Decimal] = defaultdict(Decimal)
    task_hours: dict[str, Decimal] = defaultdict(Decimal)
    task_billable_default: dict[str, bool] = {}
    task_names: dict[str, str] = {}
    task_money: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: {"billable": Decimal(0), "cost": Decimal(0)},
    )
    task_user_hours: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    task_user_bill: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    task_user_cost: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    user_hours: dict[int, dict[str, Decimal]] = defaultdict(
        lambda: {"total": Decimal(0), "billable": Decimal(0), "non_billable": Decimal(0)}
    )
    user_bill: defaultdict[int, Decimal] = defaultdict(lambda: Decimal(0))
    user_cost: defaultdict[int, Decimal] = defaultdict(lambda: Decimal(0))

    for e in entries:
        h = _entry_hours(e)
        uid = e.auth_user_id
        tid = (
            str(e.task_id)
            if e.task_id
            else ("__unassigned_billable__" if e.is_billable else "__unassigned_non_billable__")
        )
        task = tasks_map.get(str(e.task_id)) if e.task_id else None
        split = package_splits.get(str(e.id)) if package_splits else None

        tot += h
        user_hours[uid]["total"] += h
        ws = _week_start_monday(e.work_date)
        week_hours[ws]["total"] += h
        task_hours[tid] += h
        if e.task_id and task is not None:
            task_names[tid] = (task.name or "").strip() or tid
            task_billable_default[tid] = bool(getattr(task, "billable_by_default", True))
        elif tid.startswith("__unassigned_"):
            task_names[tid] = (
                "Без задачи (оплачиваемые)" if e.is_billable else "Без задачи (неоплачиваемые)"
            )
            task_billable_default[tid] = bool(e.is_billable)

        amt = Decimal(0)
        if e.is_billable:
            bill += h
            user_hours[uid]["billable"] += h
            week_hours[ws]["billable"] += h
            amt, _cur = billable_amount_respecting_package(
                h,
                e.is_billable,
                e.work_date,
                rates_map.get(uid),
                project_currency=project_currency,
                time_entry_project_id=project_id,
                task=task,
                package_split=split,
                project_row=proj_row,
            )
            total_bill += amt
            user_bill[uid] += amt
            week_bill[ws] += amt
            task_money[tid]["billable"] += amt
            if e.id not in inv_map:
                unbilled_bill += amt
        else:
            nonb += h
            user_hours[uid]["non_billable"] += h
            week_hours[ws]["non_billable"] += h

        c_amt, c_rate, _c_cur = _cost_amount_for_entry(
            h, e.work_date, cost_rates_map.get(uid), project_currency=project_currency,
        )
        total_cost += c_amt
        user_cost[uid] += c_amt
        if h > 0 and c_rate is None:
            cost_any_incomplete = True

        task_money[tid]["cost"] += c_amt
        task_user_hours[tid][uid] += h
        task_user_bill[tid][uid] += amt
        task_user_cost[tid][uid] += c_amt

    team_member_ids = sorted(set(from_access) | set(from_entries) | set(user_hours.keys()))

    user_repo = TimeTrackingUserRepository(session)
    by_auth = {
        u.auth_user_id: u
        for u in await user_repo.list_by_auth_user_ids(team_member_ids)
    }
    initials_map = await _load_initials_map(session)

    def _member_sort_key_uid(uid: int) -> str:
        u = by_auth.get(uid)
        return (u.display_name or u.email or str(uid)).lower() if u else str(uid).lower()

    def _task_members(task_key: str) -> list[dict]:
        hours_by_user = task_user_hours.get(task_key)
        if not hours_by_user:
            return []
        members: list[dict] = []
        for uid, hrs in sorted(hours_by_user.items(), key=lambda item: _member_sort_key_uid(item[0])):
            if hrs <= 0:
                continue
            u = by_auth.get(uid)
            label = (u.display_name or u.email or str(uid)) if u else str(uid)
            members.append(
                {
                    "user_id": str(uid),
                    "name": label,
                    "initials": resolve_user_initials(u, initials_map=initials_map),
                    "hours": _hours_json(hrs),
                    "billable_amount": float(_money(task_user_bill.get(task_key, {}).get(uid, _ZERO))),
                    "internal_cost_amount": float(_money(task_user_cost.get(task_key, {}).get(uid, _ZERO))),
                }
            )
        return members

    team: list[dict] = []
    for uid in sorted(team_member_ids, key=_member_sort_key_uid):
        uh = user_hours.get(uid, {"total": _ZERO, "billable": _ZERO, "non_billable": _ZERO})
        u = by_auth.get(uid)
        label = (u.display_name or u.email or str(uid)) if u else str(uid)
        team.append(
            {
                "user_id": str(uid),
                "name": label,
                "initials": resolve_user_initials(u, initials_map=initials_map),
                "hours": _hours_json(uh["total"]),
                "billable_hours": _hours_json(uh["billable"]),
                "non_billable_hours": _hours_json(uh["non_billable"]),
                "billable_amount": float(_money(user_bill.get(uid, _ZERO))),
                "internal_cost_amount": float(_money(user_cost.get(uid, _ZERO))),
                "has_project_access": uid in set(from_access),
            }
        )

    hours_by_week = [
        {
            "week_start": wk.isoformat(),
            "hours": _hours_json(vals["total"]),
            "billable_hours": _hours_json(vals["billable"]),
            "non_billable_hours": _hours_json(vals["non_billable"]),
        }
        for wk, vals in sorted(week_hours.items(), key=lambda item: item[0])
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
    seen_task_ids: set[str] = set()
    for tid, hrs in sorted(task_hours.items(), key=lambda item: str(item[0])):
        if hrs <= 0:
            continue
        if tid.startswith("__unassigned_"):
            continue
        seen_task_ids.add(tid)
        tm = task_money.get(str(tid), {"billable": Decimal(0), "cost": Decimal(0)})
        task_rows.append(
            {
                "task_id": tid,
                "name": task_names.get(tid) or tid,
                "billable": task_billable_default.get(tid, True),
                "hours": _hours_json(hrs),
                "billable_amount": float(_money(tm["billable"])),
                "internal_cost_amount": float(_money(tm["cost"])),
                "members": _task_members(str(tid)),
            }
        )

    for task in await task_repo.list_for_project(project_id):
        tid = str(task.id)
        if tid in seen_task_ids:
            continue
        task_rows.append(
            {
                "task_id": tid,
                "name": task.name,
                "billable": bool(task.billable_by_default),
                "hours": _hours_json(_ZERO),
                "billable_amount": 0.0,
                "internal_cost_amount": 0.0,
                "members": [],
            }
        )
    task_rows.sort(key=lambda row: (not row["billable"], str(row["name"]).lower()))
    for synthetic in ("__unassigned_billable__", "__unassigned_non_billable__"):
        hrs = task_hours.get(synthetic, _ZERO)
        if hrs <= 0:
            continue
        is_b = synthetic == "__unassigned_billable__"
        tm = task_money.get(synthetic, {"billable": Decimal(0), "cost": Decimal(0)})
        task_rows.append(
            {
                "task_id": synthetic,
                "name": task_names.get(synthetic)
                or ("Без задачи (оплачиваемые)" if is_b else "Без задачи (неоплачиваемые)"),
                "billable": is_b,
                "hours": _hours_json(hrs),
                "billable_amount": float(_money(tm["billable"])),
                "internal_cost_amount": float(_money(tm["cost"])),
                "members": _task_members(synthetic),
            }
        )

    raw_exp = await _fetch_expense_report_data(df_eff, dt_eff, None, [project_id])
    exp_uzs = Decimal(0)
    exp_equiv = Decimal(0)
    exp_n = 0
    pid_needle = str(project_id).strip().lower()
    for row in raw_exp:
        if str(row.get("project_id") or "").strip().lower() != pid_needle:
            continue
        exp_uzs += _d(row.get("amount_uzs", 0) or 0)
        exp_equiv += _d(row.get("equivalent_amount", 0) or 0)
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
            "expense_equivalent_total": float(_money(exp_equiv)),
            "expense_count": exp_n,
        },
        "progress_by_week": progress_by_week,
        "hours_by_week": hours_by_week,
        "tasks": task_rows,
        "team": team,
        "invoices": invoices_out,
    }
