

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.entry_pricing import (
    _billable_amount_for_entry,
    _billable_rate_for_entry,
    _cost_amount_for_entry,
)
from infrastructure.config import get_settings
from infrastructure.models import (
    TimeEntryModel,
    TimeManagerClientModel,
    TimeManagerClientProjectModel,
    TimeManagerClientTaskModel,
    TimeTrackingUserModel,
    UserHourlyRateModel,
    WeeklyTimeSubmissionModel,
)
from infrastructure.models_invoices import InvoiceLineItemModel, InvoiceModel

_log = logging.getLogger(__name__)

REPORT_TYPES = frozenset({
    "time", "detailed-expense", "contractor", "uninvoiced",
})
GROUP_OPTIONS = frozenset({"clients", "projects"})

_Q2 = Decimal("0.01")
_Q6 = Decimal("0.000001")
_ZERO = Decimal(0)


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal(0)


def _hours(v: Decimal) -> float:
    return float(v.quantize(_Q6, rounding=ROUND_HALF_UP))


def _money(v: Decimal) -> float:
    return float(v.quantize(_Q2, rounding=ROUND_HALF_UP))


def _base_entry_conditions(
    date_from: date,
    date_to: date,
    user_ids: list[int] | None,
    project_ids: list[str] | None,
    client_ids: list[str] | None,
    include_fixed_fee: bool,
    *,
    exclude_invoiced_time: bool = False,
) -> list:
    cond: list = [
        TimeEntryModel.work_date >= date_from,
        TimeEntryModel.work_date <= date_to,
        TimeEntryModel.voided_at.is_(None),
    ]
    if user_ids:
        cond.append(TimeEntryModel.auth_user_id.in_(user_ids))
    if project_ids:
        cond.append(TimeEntryModel.project_id.in_(project_ids))
    if client_ids:
        cond.append(
            TimeEntryModel.project_id.in_(
                select(TimeManagerClientProjectModel.id).where(
                    TimeManagerClientProjectModel.client_id.in_(client_ids)
                )
            )
        )
    if not include_fixed_fee:
        cond.append(
            TimeEntryModel.project_id.notin_(
                select(TimeManagerClientProjectModel.id).where(
                    TimeManagerClientProjectModel.project_type == "fixed_fee"
                )
            )
        )
    if exclude_invoiced_time:
        inv_exists = exists(
            select(1)
            .select_from(InvoiceLineItemModel)
            .join(InvoiceModel, InvoiceModel.id == InvoiceLineItemModel.invoice_id)
            .where(
                InvoiceLineItemModel.time_entry_id == TimeEntryModel.id,
                InvoiceModel.status != "canceled",
            )
        )
        cond.append(~inv_exists)
    return cond


def _voided_entry_conditions(
    date_from: date,
    date_to: date,
    user_ids: list[int] | None,
    project_ids: list[str] | None,
    client_ids: list[str] | None,
    include_fixed_fee: bool,
) -> list:

    cond: list = [
        TimeEntryModel.work_date >= date_from,
        TimeEntryModel.work_date <= date_to,
        TimeEntryModel.voided_at.isnot(None),
    ]
    if user_ids:
        cond.append(TimeEntryModel.auth_user_id.in_(user_ids))
    if project_ids:
        cond.append(TimeEntryModel.project_id.in_(project_ids))
    if client_ids:
        cond.append(
            TimeEntryModel.project_id.in_(
                select(TimeManagerClientProjectModel.id).where(
                    TimeManagerClientProjectModel.client_id.in_(client_ids)
                )
            )
        )
    if not include_fixed_fee:
        cond.append(
            TimeEntryModel.project_id.notin_(
                select(TimeManagerClientProjectModel.id).where(
                    TimeManagerClientProjectModel.project_type == "fixed_fee"
                )
            )
        )
    return cond


async def _load_user_rates(
    session: AsyncSession, user_ids: list[int] | None
) -> dict[int, list[UserHourlyRateModel]]:

    q = select(UserHourlyRateModel).where(
        UserHourlyRateModel.rate_kind == "billable"
    )
    if user_ids:
        q = q.where(UserHourlyRateModel.auth_user_id.in_(user_ids))
    rows = (await session.execute(q)).scalars().all()
    out: dict[int, list[UserHourlyRateModel]] = {}
    for r in rows:
        out.setdefault(r.auth_user_id, []).append(r)
    return out


async def _load_user_cost_rates(
    session: AsyncSession, user_ids: list[int] | None
) -> dict[int, list[UserHourlyRateModel]]:

    q = select(UserHourlyRateModel).where(UserHourlyRateModel.rate_kind == "cost")
    if user_ids:
        q = q.where(UserHourlyRateModel.auth_user_id.in_(user_ids))
    rows = (await session.execute(q)).scalars().all()
    out: dict[int, list[UserHourlyRateModel]] = {}
    for r in rows:
        out.setdefault(r.auth_user_id, []).append(r)
    return out


async def _invoice_info_for_time_entries(
    session: AsyncSession, entry_ids: list[str],
) -> dict[str, tuple[str, str]]:

    if not entry_ids:
        return {}
    q = (
        select(
            InvoiceLineItemModel.time_entry_id,
            InvoiceModel.id,
            InvoiceModel.invoice_number,
        )
        .select_from(InvoiceLineItemModel)
        .join(InvoiceModel, InvoiceModel.id == InvoiceLineItemModel.invoice_id)
        .where(
            InvoiceLineItemModel.time_entry_id.in_(entry_ids),
            InvoiceLineItemModel.time_entry_id.is_not(None),
            InvoiceModel.status != "canceled",
        )
    )
    rows = (await session.execute(q)).all()
    out: dict[str, tuple[str, str]] = {}
    for tid, iid, num in rows:
        if not tid:
            continue
        k = str(tid)
        if k not in out:
            out[k] = (str(iid), str(num))
    return out


async def invoice_details_for_time_entries(
    session: AsyncSession, entry_ids: list[str]
) -> dict[str, dict[str, Any]]:

    if not entry_ids:
        return {}
    q = (
        select(
            InvoiceLineItemModel.time_entry_id,
            InvoiceModel.id,
            InvoiceModel.invoice_number,
            InvoiceModel.status,
            InvoiceModel.amount_paid,
            InvoiceModel.total_amount,
        )
        .select_from(InvoiceLineItemModel)
        .join(InvoiceModel, InvoiceModel.id == InvoiceLineItemModel.invoice_id)
        .where(
            InvoiceLineItemModel.time_entry_id.in_(entry_ids),
            InvoiceLineItemModel.time_entry_id.is_not(None),
            InvoiceModel.status != "canceled",
        )
    )
    rows = (await session.execute(q)).all()
    out: dict[str, dict[str, Any]] = {}
    for tid, iid, inum, st, ap, tot in rows:
        if not tid:
            continue
        k = str(tid)
        if k in out:
            continue
        apd, ttd = _d(ap), _d(tot)
        is_paid = st == "paid" or (ttd > 0 and apd + _Q2 >= ttd)
        out[k] = {
            "invoice_id": str(iid),
            "invoice_number": str(inum),
            "invoice_status": str(st or ""),
            "is_paid": bool(is_paid),
        }
    return out


async def load_week_submitted_user_dates(
    session: AsyncSession,
    auth_user_ids: set[int],
    date_from: date,
    date_to: date,
) -> set[tuple[int, date]]:

    if not auth_user_ids:
        return set()
    q = select(WeeklyTimeSubmissionModel).where(
        WeeklyTimeSubmissionModel.status == "submitted",
        WeeklyTimeSubmissionModel.auth_user_id.in_(auth_user_ids),
        WeeklyTimeSubmissionModel.week_end >= date_from,
        WeeklyTimeSubmissionModel.week_start <= date_to,
    )
    rows = list((await session.execute(q)).scalars().all())
    out: set[tuple[int, date]] = set()
    for s in rows:
        d = s.week_start
        while d <= s.week_end:
            if date_from <= d <= date_to:
                out.add((s.auth_user_id, d))
            d += timedelta(days=1)
    return out


async def _fetch_expense_report_data(
    date_from: date,
    date_to: date,
    user_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
) -> list[dict]:
    settings = get_settings()
    base = (settings.expenses_service_url or "").rstrip("/")
    if not base:
        _log.error(
            "expenses report: `expenses_service_url` is empty; set EXPENSES_SERVICE_URL "
            "(e.g. http://expenses:1242) for the time_tracking service or expense report stays empty"
        )
        return []
    params: dict[str, str] = {
        "dateFrom": date_from.isoformat(),
        "dateTo": date_to.isoformat(),
    }
    if user_ids:
        params["userIds"] = ",".join(str(u) for u in user_ids)
    if project_ids:
        params["projectIds"] = ",".join(project_ids)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{base}/expenses/report-data", params=params)
        if r.status_code != 200:
            _log.error(
                "expenses report-data: HTTP %s from %s — %s",
                r.status_code,
                f"{base}/expenses/report-data",
                (r.text or "")[:800],
            )
            return []
        data = r.json()
        rows = data if isinstance(data, list) else data.get("rows", [])
        return [r for r in rows if (str(r.get("project_id") or "")).strip()]
    except Exception as exc:
        _log.exception("expenses report-data: request failed (%s): %s", base, exc)
        return []


def filter_expense_rows_to_tt_projects(
    rows: list[dict],
    projects_map: dict[str, Any],
) -> list[dict]:

    return [r for r in rows if (str(r.get("project_id") or "")).strip() in projects_map]


async def _load_users_map(session: AsyncSession) -> dict[int, TimeTrackingUserModel]:
    rows = (await session.execute(select(TimeTrackingUserModel))).scalars().all()
    return {u.auth_user_id: u for u in rows}


async def _load_projects_map(session: AsyncSession) -> dict[str, TimeManagerClientProjectModel]:
    rows = (await session.execute(select(TimeManagerClientProjectModel))).scalars().all()
    return {p.id: p for p in rows}


async def _load_clients_map(session: AsyncSession) -> dict[str, TimeManagerClientModel]:
    rows = (await session.execute(select(TimeManagerClientModel))).scalars().all()
    return {c.id: c for c in rows}


async def _load_tasks_map(session: AsyncSession) -> dict[str, TimeManagerClientTaskModel]:

    rows = (await session.execute(select(TimeManagerClientTaskModel))).scalars().all()
    return {t.id: t for t in rows}


async def build_report_summary(
    session: AsyncSession,
    *,
    report_type: str,
    date_from: date,
    date_to: date,
    user_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    client_ids: list[str] | None = None,
    include_fixed_fee: bool = True,
) -> dict:
    period = {"dateFrom": date_from.isoformat(), "dateTo": date_to.isoformat()}

    if report_type == "detailed-expense":
        return await _summary_expense(
            session, period, date_from, date_to, user_ids, project_ids,
        )

    cond = _base_entry_conditions(
        date_from,
        date_to,
        user_ids,
        project_ids,
        client_ids,
        include_fixed_fee,
        exclude_invoiced_time=(report_type == "uninvoiced"),
    )


    entries_q = select(TimeEntryModel).where(and_(*cond))
    entries = list((await session.execute(entries_q)).scalars().all())

    rates_map = await _load_user_rates(session, user_ids)
    projects_map = await _load_projects_map(session)

    total = _ZERO
    billable = _ZERO
    non_billable = _ZERO
    billable_amount = _ZERO
    currency = "USD"

    for e in entries:
        h = _d(e.hours)
        total += h
        p = projects_map.get(e.project_id) if e.project_id else None
        pc = (getattr(p, "currency", None) or "USD") if p else "USD"
        if e.is_billable:
            billable += h
            amt, cur = _billable_amount_for_entry(
                h,
                True,
                e.work_date,
                rates_map.get(e.auth_user_id),
                project_currency=pc,
                time_entry_project_id=e.project_id,
            )
            billable_amount += amt
            if cur != "USD" or (pc and pc != "USD"):
                currency = cur
        else:
            non_billable += h

    base: dict[str, Any] = {
        "reportType": report_type,
        "period": period,
        "totalHours": _hours(total),
        "billableHours": _hours(billable),
        "nonBillableHours": _hours(non_billable),
        "billableAmount": {"value": _money(billable_amount), "currency": currency},
    }

    if report_type == "time":
        base["unbilledAmount"] = {"value": _money(billable_amount), "currency": currency}
    elif report_type == "contractor":
        base["contractorHours"] = _hours(total)
        base["contractorCost"] = {"value": 0, "currency": currency}
    elif report_type == "uninvoiced":
        base["uninvoicedHours"] = _hours(billable)
        base["amountToInvoice"] = {"value": _money(billable_amount), "currency": currency}

    return base


async def _summary_expense(
    session: AsyncSession,
    period: dict,
    date_from: date,
    date_to: date,
    user_ids: list[int] | None,
    project_ids: list[str] | None,
) -> dict:
    rows = await _fetch_expense_report_data(date_from, date_to, user_ids, project_ids)
    projects_map = await _load_projects_map(session)
    rows = filter_expense_rows_to_tt_projects(rows, projects_map)
    total_uzs = Decimal(0)
    reimbursable = Decimal(0)
    count = 0
    for r in rows:
        amt = _d(r.get("amount_uzs", 0))
        total_uzs += amt
        if r.get("is_reimbursable"):
            reimbursable += amt
        count += 1
    return {
        "reportType": "detailed-expense",
        "period": period,
        "totalExpenseUzs": _money(total_uzs),
        "reimbursableUzs": _money(reimbursable),
        "nonReimbursableUzs": _money(total_uzs - reimbursable),
        "lineCount": count,
    }


async def build_report_table(
    session: AsyncSession,
    *,
    report_type: str,
    group: str | None,
    date_from: date,
    date_to: date,
    user_ids: list[int] | None = None,
    project_ids: list[str] | None = None,
    client_ids: list[str] | None = None,
    include_fixed_fee: bool = True,
    sort: str = "date_asc",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    if report_type == "detailed-expense":
        return await _table_expense(
            session, date_from, date_to, user_ids, project_ids, sort, page, page_size,
        )

    if report_type in ("time", "contractor", "uninvoiced"):
        return await _table_aggregated(
            session,
            report_type=report_type,
            group=group or "projects",
            date_from=date_from,
            date_to=date_to,
            user_ids=user_ids,
            project_ids=project_ids,
            client_ids=client_ids,
            include_fixed_fee=include_fixed_fee,
            sort=sort,
            page=page,
            page_size=page_size,
        )

    raise ValueError(f"Unsupported report_type for table: {report_type!r}")


async def _table_aggregated(
    session: AsyncSession,
    *,
    report_type: str,
    group: str,
    date_from: date,
    date_to: date,
    user_ids: list[int] | None,
    project_ids: list[str] | None,
    client_ids: list[str] | None,
    include_fixed_fee: bool,
    sort: str,
    page: int,
    page_size: int,
) -> dict:
    cond = _base_entry_conditions(
        date_from,
        date_to,
        user_ids,
        project_ids,
        client_ids,
        include_fixed_fee,
        exclude_invoiced_time=(report_type == "uninvoiced"),
    )

    if report_type == "uninvoiced":
        cond.append(TimeEntryModel.is_billable.is_(True))


    entries_q = select(TimeEntryModel).where(and_(*cond))
    entries = list((await session.execute(entries_q)).scalars().all())

    projects_map = await _load_projects_map(session)
    clients_map = await _load_clients_map(session)
    rates_map = await _load_user_rates(session, user_ids)

    buckets: dict[Any, dict] = {}
    for e in entries:
        if group == "projects":
            gid = e.project_id
        elif group == "clients":
            p = projects_map.get(e.project_id) if e.project_id else None
            gid = p.client_id if p else None
        else:
            raise ValueError(f"Unsupported table group: {group!r}")

        bkt = buckets.get(gid)
        if bkt is None:
            bkt = {"total": _ZERO, "billable": _ZERO, "amount": _ZERO, "currency": "USD"}
            buckets[gid] = bkt

        h = _d(e.hours)
        bkt["total"] += h
        p_ent = projects_map.get(e.project_id) if e.project_id else None
        pc = (getattr(p_ent, "currency", None) or "USD") if p_ent else "USD"
        if e.is_billable:
            bkt["billable"] += h
            amt, cur = _billable_amount_for_entry(
                h,
                True,
                e.work_date,
                rates_map.get(e.auth_user_id),
                project_currency=pc,
                time_entry_project_id=e.project_id,
            )
            bkt["amount"] += amt
            if cur != "USD" or (pc and pc != "USD"):
                bkt["currency"] = cur

    sorted_keys = sorted(
        buckets.keys(),
        key=lambda k: float(buckets[k]["total"]),
        reverse=(sort != "hours_asc"),
    )

    total_count = len(sorted_keys)
    page_keys = sorted_keys[(page - 1) * page_size: page * page_size]

    rows: list[dict] = []
    for gid in page_keys:
        bkt = buckets[gid]
        total_h = bkt["total"]
        bill_h = bkt["billable"]
        row: dict[str, Any] = {
            "hours": _hours(total_h),
            "billableHours": _hours(bill_h),
            "nonBillableHours": _hours(total_h - bill_h),
            "billableAmount": _money(bkt["amount"]),
            "currency": bkt["currency"],
            "invoicedAmount": 0,
        }
        if group == "projects":
            p = projects_map.get(gid) if gid else None
            row["projectId"] = gid
            row["name"] = p.name if p else "Без проекта"
            row["code"] = p.code if p else None
            if p and p.client_id:
                c = clients_map.get(p.client_id)
                row["clientId"] = p.client_id
                row["clientName"] = c.name if c else None
        elif group == "clients":
            c = clients_map.get(gid) if gid else None
            row["clientId"] = gid
            row["name"] = c.name if c else "Без клиента"
        else:
            raise ValueError(f"Unsupported table group: {group!r}")
        rows.append(row)

    return {
        "rows": rows,
        "totalCount": total_count,
        "page": page,
        "pageSize": page_size,
        "hasMore": (page * page_size) < total_count,
    }


async def _table_expense(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    user_ids: list[int] | None,
    project_ids: list[str] | None,
    sort: str,
    page: int,
    page_size: int,
) -> dict:
    all_rows = await _fetch_expense_report_data(date_from, date_to, user_ids, project_ids)
    projects_map = await _load_projects_map(session)
    all_rows = filter_expense_rows_to_tt_projects(all_rows, projects_map)

    reverse = sort.endswith("_desc")
    all_rows.sort(key=lambda r: r.get("expense_date", ""), reverse=reverse)

    total_count = len(all_rows)
    start = (page - 1) * page_size
    page_rows = all_rows[start: start + page_size]

    rows: list[dict] = []
    for r in page_rows:
        rows.append({
            "rowId": r.get("id", ""),
            "sourceType": "expense",
            "sourceId": r.get("id", ""),
            "date": r.get("expense_date", ""),
            "projectId": r.get("project_id"),
            "expenseCategoryId": r.get("expense_category_id"),
            "expenseType": r.get("expense_type", ""),
            "description": r.get("description", ""),
            "amountUzs": _money(_d(r.get("amount_uzs", 0))),
            "exchangeRate": float(r.get("exchange_rate", 0)),
            "equivalentAmount": _money(_d(r.get("equivalent_amount", 0))),
            "status": r.get("status", ""),
            "authorUserId": r.get("created_by_user_id"),
            "isReimbursable": r.get("is_reimbursable", False),
        })

    return {
        "rows": rows,
        "totalCount": total_count,
        "page": page,
        "pageSize": page_size,
        "hasMore": (page * page_size) < total_count,
    }


async def build_table_rows_flat(
    session: AsyncSession, **kwargs
) -> list[dict]:

    kwargs["page"] = 1
    kwargs["page_size"] = 100_000
    result = await build_report_table(session, **kwargs)
    return result.get("rows", [])
