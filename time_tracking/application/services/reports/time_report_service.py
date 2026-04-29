

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.entry_pricing import (
    _billable_amount_for_entry,
    _billable_rate_for_entry,
    _cost_amount_for_entry,
)
from application.report_builder import (
    _base_entry_conditions,
    _voided_entry_conditions,
    _d,
    _load_user_cost_rates,
    _load_clients_map,
    _load_projects_map,
    _load_tasks_map,
    _load_user_rates,
    _load_users_map,
    invoice_details_for_time_entries,
    load_week_submitted_user_dates,
)
from infrastructure.models import TimeEntryModel
from application.services.reports._base import (
    _hours,
    _money,
    _percent_billable,
    _ZERO,
    build_response,
)

TIME_GROUP_OPTIONS = frozenset({"clients", "projects"})


MAX_ENTRY_LOG_ROWS = 100_000


TIME_REPORT_FLAT_COLUMNS: tuple[str, ...] = (
    "work_date",
    "recorded_at",
    "client_id",
    "client_name",
    "project_id",
    "project_name",
    "project_code",
    "task_id",
    "task_name",
    "note",
    "hours",
    "is_billable",
    "task_billable_by_default",
    "is_invoiced",
    "is_paid",
    "is_week_submitted",
    "employee_name",
    "employee_position",
    "auth_user_id",
    "billable_rate",
    "amount_to_pay",
    "cost_rate",
    "cost_amount",
    "currency",
    "external_reference_url",
    "invoice_id",
    "invoice_number",
    "time_entry_id",
    "source_entry_count",
    "report_group_by",
    "report_group_id",
    "is_voided",
    "voided_at",
    "voided_by_auth_user_id",
    "voided_by_name",
    "void_kind",
)


def _time_entry_line_snake(
    e: TimeEntryModel,
    *,
    projects_map: dict[str, Any],
    clients_map: dict[str, Any],
    tasks_map: dict[str, Any],
    users_map: dict[int, Any],
    rates_map: dict[int, list],
    cost_rates_map: dict[int, list],
    invoice_by_entry: dict[str, dict[str, Any]],
    week_submitted: set[tuple[int, date]],
) -> dict[str, Any]:

    p = projects_map.get(e.project_id) if e.project_id else None
    c = clients_map.get(p.client_id) if p and p.client_id else None
    t = tasks_map.get(e.task_id) if e.task_id else None
    u = users_map.get(e.auth_user_id)
    project_currency = (getattr(p, "currency", None) or "USD") if p else "USD"
    h = _d(e.hours)
    uid = e.auth_user_id
    brates = rates_map.get(uid) or []
    crates = cost_rates_map.get(uid) or []
    amt, _cur = _billable_amount_for_entry(
        h,
        e.is_billable,
        e.work_date,
        brates,
        project_currency=project_currency,
        time_entry_project_id=e.project_id,
    )
    br, _brc = _billable_rate_for_entry(
        e.work_date,
        brates,
        project_currency=project_currency,
        time_entry_project_id=e.project_id,
    )
    cost_amt, cost_r, _cnc = _cost_amount_for_entry(
        h, e.work_date, crates, project_currency=project_currency
    )
    inv = invoice_by_entry.get(e.id) or {}
    is_invoiced = e.id in invoice_by_entry
    is_paid = bool(inv.get("is_paid")) if is_invoiced else False
    wk_ok = (uid, e.work_date) in week_submitted
    desc = (e.description or "").strip()
    ext = (e.external_reference_url or "").strip() or None
    is_voided = e.voided_at is not None
    voider = users_map.get(e.voided_by_auth_user_id) if e.voided_by_auth_user_id else None
    voider_name = (voider.display_name or voider.email) if voider else None

    return {
        "time_entry_id": e.id,
        "work_date": e.work_date.isoformat(),
        "recorded_at": e.created_at.isoformat(),
        "client_id": c.id if c else None,
        "client_name": c.name if c else None,
        "project_id": e.project_id,
        "project_name": p.name if p else None,
        "project_code": p.code if p else None,
        "task_id": e.task_id,
        "task_name": t.name if t else None,
        "note": desc or None,
        "hours": _hours(h),
        "is_billable": e.is_billable,
        "task_billable_by_default": bool(t.billable_by_default) if t else None,
        "is_invoiced": is_invoiced,
        "is_paid": is_paid,
        "is_week_submitted": wk_ok,
        "employee_name": (u.display_name or u.email) if u else str(uid),
        "employee_position": ((u.position or "").strip() or None) if u else None,
        "auth_user_id": uid,
        "billable_rate": _money(br) if br is not None else None,
        "amount_to_pay": _money(amt),
        "cost_rate": _money(cost_r) if cost_r is not None else None,
        "cost_amount": _money(cost_amt),
        "currency": project_currency,
        "external_reference_url": ext,
        "invoice_id": inv.get("invoice_id"),
        "invoice_number": inv.get("invoice_number"),
        "source_entry_count": 1,
        "is_voided": is_voided,
        "voided_at": e.voided_at.isoformat() if e.voided_at else None,
        "voided_by_auth_user_id": e.voided_by_auth_user_id,
        "voided_by_name": voider_name,
        "void_kind": e.void_kind,
    }


def _aggregate_entries_to_snake_line(
    entries: list[TimeEntryModel],
    line_ctx: dict[str, Any],
) -> dict[str, Any]:

    if not entries:
        return {}
    projects_map: dict = line_ctx["projects_map"]
    clients_map: dict = line_ctx["clients_map"]
    tasks_map: dict = line_ctx["tasks_map"]
    users_map: dict = line_ctx["users_map"]
    rates_map: dict = line_ctx["rates_map"]
    cost_rates_map: dict = line_ctx["cost_rates_map"]
    invoice_by_entry: dict = line_ctx["invoice_by_entry"]
    week_set: set[tuple[int, date]] = line_ctx["week_submitted"]

    uid = entries[0].auth_user_id
    p = projects_map.get(entries[0].project_id) if entries[0].project_id else None
    c = clients_map.get(p.client_id) if p and p.client_id else None
    u = users_map.get(uid)
    project_currency = (getattr(p, "currency", None) or "USD") if p else "USD"

    total_h = _ZERO
    billable_h = _ZERO
    total_amt = _ZERO
    total_cost = _ZERO
    work_dates: list[date] = []
    created_list: list = []
    invoiced: list[TimeEntryModel] = []
    tids: set = set()

    for e in entries:
        h = _d(e.hours)
        work_dates.append(e.work_date)
        created_list.append(e.created_at)
        tids.add(e.task_id)
        if e.is_billable:
            total_h += h
            billable_h += h
            brt = rates_map.get(uid) or []
            a, _c = _billable_amount_for_entry(
                h,
                True,
                e.work_date,
                brt,
                project_currency=project_currency,
                time_entry_project_id=e.project_id,
            )
            total_amt += a
        else:
            total_h += h
        cr = cost_rates_map.get(uid) or []
        ca, _, _ = _cost_amount_for_entry(
            h, e.work_date, cr, project_currency=project_currency
        )
        total_cost += ca
        if e.id in invoice_by_entry:
            invoiced.append(e)

    n = len(entries)
    wmin = min(work_dates)
    wmax = max(work_dates)
    rmax = max(created_list) if created_list else None
    is_inv = len(invoiced) > 0
    is_paid = is_inv and all(
        bool(invoice_by_entry[x.id].get("is_paid", False)) for x in invoiced
    )
    all_week = all((uid, e.work_date) in week_set for e in entries)

    if billable_h > 0:
        eff_bill: float | None = _money(total_amt / billable_h)
    else:
        eff_bill = None
    if total_h > 0:
        eff_cost_r: float | None = _money(total_cost / total_h)
    else:
        eff_cost_r = None

    t_id = next(iter(tids)) if len(tids) == 1 else None
    t = tasks_map.get(t_id) if t_id else None
    tname: str | None
    if len(tids) == 1 and t_id and t:
        tname = t.name
    elif len(tids) > 1:
        tname = f"({len(tids)} задач)"
    else:
        tname = None
    t_bill_def: bool | None
    if t is not None:
        t_bill_def = bool(t.billable_by_default)
    else:
        t_bill_def = None
    is_bill_only = total_h > 0 and (billable_h == total_h)

    return {
        "time_entry_id": None,
        "work_date": wmin.isoformat(),
        "recorded_at": rmax.isoformat() if rmax else wmax.isoformat(),
        "client_id": c.id if c else None,
        "client_name": c.name if c else None,
        "project_id": entries[0].project_id,
        "project_name": p.name if p else None,
        "project_code": p.code if p else None,
        "task_id": t_id,
        "task_name": tname,
        "note": None,
        "hours": _hours(total_h),
        "is_billable": bool(is_bill_only),
        "task_billable_by_default": t_bill_def,
        "is_invoiced": is_inv,
        "is_paid": is_paid,
        "is_week_submitted": all_week,
        "employee_name": (u.display_name or u.email) if u else str(uid),
        "employee_position": ((u.position or "").strip() or None) if u else None,
        "auth_user_id": uid,
        "billable_rate": eff_bill,
        "amount_to_pay": _money(total_amt),
        "cost_rate": eff_cost_r,
        "cost_amount": _money(total_cost),
        "currency": project_currency,
        "external_reference_url": None,
        "invoice_id": None,
        "invoice_number": None,
        "source_entry_count": n,
        "is_voided": False,
        "voided_at": None,
        "voided_by_auth_user_id": None,
        "voided_by_name": None,
        "void_kind": None,
    }


def _line_snake_to_api_json(line: dict[str, Any]) -> dict[str, Any]:

    out: dict[str, Any] = {
        "timeEntryId": line.get("time_entry_id"),
        "workDate": line["work_date"],
        "recordedAt": line["recorded_at"],
        "clientId": line["client_id"],
        "clientName": line["client_name"],
        "projectId": line["project_id"],
        "projectName": line["project_name"],
        "projectCode": line["project_code"],
        "taskId": line["task_id"],
        "taskName": line["task_name"],
        "note": line["note"],
        "hours": line["hours"],
        "isBillable": line["is_billable"],
        "taskBillableByDefault": line["task_billable_by_default"],
        "isInvoiced": line["is_invoiced"],
        "isPaid": line["is_paid"],
        "isWeekSubmitted": line["is_week_submitted"],
        "employeeName": line["employee_name"],
        "employeePosition": line["employee_position"] or None,
        "authUserId": line["auth_user_id"],
        "billableRate": line["billable_rate"],
        "amountToPay": line["amount_to_pay"],
        "costRate": line["cost_rate"],
        "costAmount": line["cost_amount"],
        "currency": line["currency"],
        "externalReferenceUrl": line["external_reference_url"],
        "invoiceId": line.get("invoice_id"),
        "invoiceNumber": line.get("invoice_number"),
        "sourceEntryCount": line.get("source_entry_count", 1),
        "isVoided": line.get("is_voided", False),
        "voidedAt": line.get("voided_at"),
        "voidedByAuthUserId": line.get("voided_by_auth_user_id"),
        "voidedByName": line.get("voided_by_name"),
        "voidKind": line.get("void_kind"),
    }

    eid = line.get("time_entry_id")
    out["id"] = eid
    out["time_entry_id"] = eid
    out["work_date"] = line["work_date"]
    out["recorded_at"] = line["recorded_at"]
    out["is_billable"] = line["is_billable"]
    out["client_id"] = line["client_id"]
    out["client_name"] = line["client_name"]
    out["project_id"] = line["project_id"]
    out["project_name"] = line["project_name"]
    out["task_id"] = line["task_id"]
    out["task_name"] = line["task_name"]
    out["description"] = line["note"]
    out["is_voided"] = line.get("is_voided", False)
    out["voided_at"] = line.get("voided_at")
    return out


async def get_time_report(
    session: AsyncSession,
    *,
    group_by: str,
    date_from: date,
    date_to: date,
    client_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    user_ids: list[int] | None = None,
    task_ids: list[str] | None = None,
    is_billable: bool | None = None,
    include_fixed_fee: bool = True,
    page: int = 1,
    per_page: int = 100,
) -> dict:
    cond = _base_entry_conditions(
        date_from, date_to, user_ids, project_ids, client_ids, include_fixed_fee,
    )
    if is_billable is not None:
        cond.append(TimeEntryModel.is_billable.is_(is_billable))
    if task_ids:
        cond.append(TimeEntryModel.task_id.in_(task_ids))

    entries_q = select(TimeEntryModel).where(and_(*cond))
    entries = list((await session.execute(entries_q)).scalars().all())

    vcond = _voided_entry_conditions(
        date_from, date_to, user_ids, project_ids, client_ids, include_fixed_fee
    )
    if is_billable is not None:
        vcond.append(TimeEntryModel.is_billable.is_(is_billable))
    if task_ids:
        vcond.append(TimeEntryModel.task_id.in_(task_ids))
    voided_entries = list(
        (await session.execute(select(TimeEntryModel).where(and_(*vcond)))).scalars().all()
    )

    users_map = await _load_users_map(session)
    projects_map = await _load_projects_map(session)
    clients_map = await _load_clients_map(session)
    tasks_map = await _load_tasks_map(session)
    uids = {e.auth_user_id for e in entries} | {e.auth_user_id for e in voided_entries}
    all_entry_ids = [e.id for e in entries] + [e.id for e in voided_entries]
    inv_map = await invoice_details_for_time_entries(session, all_entry_ids)
    week_set = await load_week_submitted_user_dates(session, uids, date_from, date_to)
    rates_map = await _load_user_rates(session, list(uids)) if uids else {}
    cost_rates_map = await _load_user_cost_rates(session, list(uids)) if uids else {}

    line_ctx = {
        "projects_map": projects_map,
        "clients_map": clients_map,
        "tasks_map": tasks_map,
        "users_map": users_map,
        "rates_map": rates_map,
        "cost_rates_map": cost_rates_map,
        "invoice_by_entry": inv_map,
        "week_submitted": week_set,
    }

    buckets: dict[Any, dict] = {}
    for e in entries:
        gid = _get_group_id(e, group_by, projects_map)
        p = projects_map.get(e.project_id) if e.project_id else None
        project_currency = (getattr(p, "currency", None) or "USD") if p else "USD"
        eline: dict[str, Any] | None = None
        if group_by != "clients":
            sline = _time_entry_line_snake(e, **line_ctx)
            eline = _line_snake_to_api_json(sline)

        bkt = buckets.setdefault(
            gid,
            {
                "total": _ZERO,
                "billable": _ZERO,
                "amount": _ZERO,
                "currency": project_currency,
                "last_recorded_at": None,
                "user_buckets": {},
                "raw_entry_count": 0,
            },
        )
        if bkt["last_recorded_at"] is None or e.created_at > bkt["last_recorded_at"]:
            bkt["last_recorded_at"] = e.created_at
        h = _d(e.hours)
        bkt["total"] += h
        bkt["raw_entry_count"] = int(bkt.get("raw_entry_count", 0)) + 1
        uid = e.auth_user_id
        if uid not in bkt["user_buckets"]:
            d: dict[str, Any] = {
                "total": _ZERO,
                "billable": _ZERO,
                "amount": _ZERO,
                "currency": project_currency,
                "last_recorded_at": None,
            }
            if group_by == "clients":
                d["project_entry_groups"] = {}
                d["raw_entry_count"] = 0
            else:
                d["entry_events"] = []
            bkt["user_buckets"][uid] = d
        ubkt = bkt["user_buckets"][uid]
        if ubkt["last_recorded_at"] is None or e.created_at > ubkt["last_recorded_at"]:
            ubkt["last_recorded_at"] = e.created_at
        if group_by == "clients":
            pid = e.project_id or ""
            ubkt["project_entry_groups"].setdefault(pid, []).append(e)
            ubkt["raw_entry_count"] = int(ubkt["raw_entry_count"]) + 1
        else:
            if eline is not None:
                ubkt["entry_events"].append(eline)
        ubkt["total"] += h
        if e.is_billable:
            bkt["billable"] += h
            br = rates_map.get(uid)
            amt, effective_cur = _billable_amount_for_entry(
                h,
                True,
                e.work_date,
                br,
                project_currency=project_currency,
                time_entry_project_id=e.project_id,
            )
            bkt["amount"] += amt
            bkt["currency"] = effective_cur or project_currency
            ubkt["billable"] += h
            ubkt["amount"] += amt
            ubkt["currency"] = effective_cur or project_currency

    all_rows: list[dict] = []
    for gid, bkt in buckets.items():
        row = _build_row(
            gid,
            bkt,
            group_by,
            users_map,
            projects_map,
            clients_map,
            line_ctx=line_ctx,
        )
        all_rows.append(row)

    all_rows.sort(key=lambda r: r.get("total_hours", 0), reverse=True)
    total_entries_count = len(all_rows)
    start = (page - 1) * per_page
    results = all_rows[start : start + per_page]

    voided_api_lines: list[dict[str, Any]] = []
    if voided_entries:
        for e in sorted(voided_entries, key=lambda x: (x.work_date, x.created_at, x.id)):
            sline = _time_entry_line_snake(e, **line_ctx)
            voided_api_lines.append(_line_snake_to_api_json(sline))

    out = build_response(
        results=results,
        total_entries=total_entries_count,
        page=page,
        per_page=per_page,
        report_type="time",
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
    )
    if voided_api_lines:
        out["voidedTimeEntries"] = voided_api_lines
        out["meta"] = {
            **out["meta"],
            "voided_time_entries_count": len(voided_api_lines),
        }
    return out


async def get_time_report_all_rows(
    session: AsyncSession, **kwargs: Any
) -> list[dict]:

    kwargs["page"] = 1
    kwargs["per_page"] = 100_000
    result = await get_time_report(session, **kwargs)
    return result.get("results", [])


async def get_time_report_flat_entries(
    session: AsyncSession,
    *,
    group_by: str,
    date_from: date,
    date_to: date,
    client_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    user_ids: list[int] | None = None,
    task_ids: list[str] | None = None,
    is_billable: bool | None = None,
    include_fixed_fee: bool = True,
) -> list[dict[str, Any]]:

    cond = _base_entry_conditions(
        date_from, date_to, user_ids, project_ids, client_ids, include_fixed_fee,
    )
    if is_billable is not None:
        cond.append(TimeEntryModel.is_billable.is_(is_billable))
    if task_ids:
        cond.append(TimeEntryModel.task_id.in_(task_ids))
    entries_q = select(TimeEntryModel).where(and_(*cond))
    entries = list((await session.execute(entries_q)).scalars().all())

    vcond = _voided_entry_conditions(
        date_from, date_to, user_ids, project_ids, client_ids, include_fixed_fee
    )
    if is_billable is not None:
        vcond.append(TimeEntryModel.is_billable.is_(is_billable))
    if task_ids:
        vcond.append(TimeEntryModel.task_id.in_(task_ids))
    voided_entries = list(
        (await session.execute(select(TimeEntryModel).where(and_(*vcond)))).scalars().all()
    )

    users_map = await _load_users_map(session)
    projects_map = await _load_projects_map(session)
    clients_map = await _load_clients_map(session)
    tasks_map = await _load_tasks_map(session)
    uids = {e.auth_user_id for e in entries} | {e.auth_user_id for e in voided_entries}
    all_entry_ids = [e.id for e in entries] + [e.id for e in voided_entries]
    inv_map = await invoice_details_for_time_entries(session, all_entry_ids)
    week_set = await load_week_submitted_user_dates(session, uids, date_from, date_to)
    rates_map = await _load_user_rates(session, list(uids)) if uids else {}
    cost_rates_map = await _load_user_cost_rates(session, list(uids)) if uids else {}
    line_ctx: dict[str, Any] = {
        "projects_map": projects_map,
        "clients_map": clients_map,
        "tasks_map": tasks_map,
        "users_map": users_map,
        "rates_map": rates_map,
        "cost_rates_map": cost_rates_map,
        "invoice_by_entry": inv_map,
        "week_submitted": week_set,
    }

    if group_by == "clients":
        by_key: dict[tuple[Any, int, str], list[TimeEntryModel]] = defaultdict(list)
        for e in entries:
            g = _get_group_id(e, "clients", projects_map)
            by_key[(g, e.auth_user_id, e.project_id or "")].append(e)
        flat2: list[dict[str, Any]] = []
        for (g, _uid, _pid), elist in by_key.items():
            sn = _aggregate_entries_to_snake_line(elist, line_ctx)
            if isinstance(g, tuple) and len(g) == 2:
                sn["report_group_id"] = f"{g[0]}|{g[1]}"
            else:
                sn["report_group_id"] = str(g) if g is not None else None
            sn["report_group_by"] = group_by
            flat2.append(sn)
        for e in voided_entries:
            sn = _time_entry_line_snake(e, **line_ctx)
            g = _get_group_id(e, "clients", projects_map)
            if isinstance(g, tuple) and len(g) == 2:
                sn["report_group_id"] = f"{g[0]}|{g[1]}"
            else:
                sn["report_group_id"] = str(g) if g is not None else None
            sn["report_group_by"] = group_by
            flat2.append(sn)
        flat2.sort(
            key=lambda r: (
                (r.get("client_name") or "") if isinstance(r.get("client_name"), str) else "",
                (r.get("project_name") or "") if isinstance(r.get("project_name"), str) else "",
                r.get("auth_user_id") or 0,
            )
        )
        return [_row_for_export(r) for r in flat2]

    flat: list[dict[str, Any]] = []
    for e in entries:
        sline = _time_entry_line_snake(e, **line_ctx)
        g = _get_group_id(e, group_by, projects_map)
        if isinstance(g, tuple) and len(g) == 2:
            a, b = g
            sline["report_group_id"] = f"{a}|{b}"
        else:
            sline["report_group_id"] = str(g) if g is not None else None
        sline["report_group_by"] = group_by
        flat.append(sline)
    for e in voided_entries:
        sline = _time_entry_line_snake(e, **line_ctx)
        g = _get_group_id(e, group_by, projects_map)
        if isinstance(g, tuple) and len(g) == 2:
            a, b = g
            sline["report_group_id"] = f"{a}|{b}"
        else:
            sline["report_group_id"] = str(g) if g is not None else None
        sline["report_group_by"] = group_by
        flat.append(sline)

    flat.sort(
        key=lambda r: (
            r.get("work_date") or "",
            r.get("auth_user_id") or 0,
            r.get("recorded_at") or "",
            r.get("time_entry_id") or "",
        )
    )
    return [_row_for_export(r) for r in flat]


def _row_for_export(r: dict[str, Any]) -> dict[str, Any]:

    return {k: r.get(k) for k in TIME_REPORT_FLAT_COLUMNS}


def _get_group_id(e: Any, group_by: str, projects_map: dict) -> Any:
    p = projects_map.get(e.project_id) if e.project_id else None
    pc = (getattr(p, "currency", None) or "USD") if p else "USD"
    if group_by == "projects":
        return e.project_id
    if group_by == "clients":
        cid = p.client_id if p else None
        return (cid, pc) if cid is not None else (None, pc)
    raise ValueError(f"Unsupported time report group_by: {group_by!r}")


def _entry_log_payload(
    ubkt: dict,
    *,
    group_by: str,
    line_ctx: dict[str, Any] | None,
    max_n: int = MAX_ENTRY_LOG_ROWS,
) -> dict[str, Any]:
    last_dt = ubkt.get("last_recorded_at")
    if group_by == "clients":
        pbg: dict[str, list] = ubkt.get("project_entry_groups") or {}
        raw_n = int(ubkt.get("raw_entry_count") or 0)
        pb: list[dict[str, Any]] = []
        if line_ctx and pbg:
            def _project_name_key(pid: str) -> str:
                if not pid:
                    return "\uffff"
                pr = line_ctx["projects_map"].get(pid)
                return (pr.name or "").lower() if pr else (pid or "")

            for _pid, elist in sorted(
                pbg.items(),
                key=lambda it: (_project_name_key(it[0]), it[0] or ""),
            ):
                if not elist:
                    continue
                sn = _aggregate_entries_to_snake_line(elist, line_ctx)
                pb.append(_line_snake_to_api_json(sn))
        return {
            "last_recorded_at": last_dt.isoformat() if last_dt else None,
            "entries": [],
            "entries_total": raw_n,
            "entries_truncated": False,
            "projectBreakdown": pb,
        }
    events = sorted(
        ubkt.get("entry_events") or [],
        key=lambda x: x.get("recordedAt", ""),
        reverse=True,
    )
    total_n = len(events)
    truncated = total_n > max_n
    return {
        "last_recorded_at": last_dt.isoformat() if last_dt else None,
        "entries": events[:max_n],
        "entries_total": total_n,
        "entries_truncated": truncated,
    }


def _build_users_list(
    user_buckets: dict,
    users_map: dict,
    *,
    group_by: str,
    line_ctx: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for uid, ubkt in user_buckets.items():
        u = users_map.get(uid)
        log = _entry_log_payload(ubkt, group_by=group_by, line_ctx=line_ctx)
        ep = ((u.position or "").strip() or None) if u else None
        result.append(
            {
                "user_id": uid,
                "user_name": (u.display_name or u.email) if u else str(uid or ""),
                "employee_position": ep,
                "employeePosition": ep,
                "avatar_url": u.picture if u else None,
                "total_hours": _hours(ubkt["total"]),
                "billable_hours": _hours(ubkt["billable"]),
                "billable_percent": _percent_billable(ubkt["total"], ubkt["billable"]),
                "billable_amount": _money(ubkt["amount"]),
                "currency": ubkt["currency"],
                **log,
            }
        )
    result.sort(key=lambda r: r["total_hours"], reverse=True)
    return result


def _build_row(
    gid: Any,
    bkt: dict,
    group_by: str,
    users_map: dict,
    projects_map: dict,
    clients_map: dict,
    *,
    line_ctx: dict[str, Any],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "total_hours": _hours(bkt["total"]),
        "billable_hours": _hours(bkt["billable"]),
        "billable_percent": _percent_billable(bkt["total"], bkt["billable"]),
        "currency": bkt["currency"],
        "billable_amount": _money(bkt["amount"]),
        "source_entry_count": int(bkt.get("raw_entry_count") or 0),
    }

    last_bucket = bkt.get("last_recorded_at")
    row["last_recorded_at"] = last_bucket.isoformat() if last_bucket else None

    if group_by == "clients":
        cid: Any
        pcur: str | None
        if isinstance(gid, tuple) and len(gid) == 2:
            cid, pcur = gid
        else:
            cid = gid
            pcur = None
        c = clients_map.get(cid) if cid else None
        row["client_id"] = cid
        row["client_name"] = c.name if c else None
        cur_g = pcur or row["currency"]
        row["group_currency"] = cur_g
        row["report_group_id"] = f"{cid!s}|{cur_g}"
        row["users"] = _build_users_list(
            bkt["user_buckets"],
            users_map,
            group_by=group_by,
            line_ctx=line_ctx,
        )
    elif group_by == "projects":
        p = projects_map.get(gid) if gid else None
        c = clients_map.get(p.client_id) if (p and p.client_id) else None
        row["client_id"] = p.client_id if p else None
        row["client_name"] = c.name if c else None
        row["project_id"] = gid
        row["project_name"] = p.name if p else None
        row["report_group_id"] = str(gid) if gid is not None else None
        row["users"] = _build_users_list(
            bkt["user_buckets"],
            users_map,
            group_by=group_by,
            line_ctx=line_ctx,
        )
    else:
        raise ValueError(f"Unsupported time report group_by: {group_by!r}")

    return row


def _row_time_report_summary_for_export(r: dict[str, Any], *, group_by: str) -> dict[str, Any]:

    if group_by == "clients":
        keys = (
            "client_id",
            "client_name",
            "group_currency",
            "report_group_id",
            "total_hours",
            "billable_hours",
            "billable_percent",
            "currency",
            "billable_amount",
            "source_entry_count",
            "last_recorded_at",
        )
    elif group_by == "projects":
        keys = (
            "client_id",
            "client_name",
            "project_id",
            "project_name",
            "report_group_id",
            "total_hours",
            "billable_hours",
            "billable_percent",
            "currency",
            "billable_amount",
            "source_entry_count",
            "last_recorded_at",
        )
    else:
        raise ValueError(f"Unsupported time report group_by: {group_by!r}")
    return {k: r.get(k) for k in keys}


async def get_time_report_summary_for_export(
    session: AsyncSession,
    *,
    group_by: str,
    date_from: date,
    date_to: date,
    client_ids: list[str] | None = None,
    project_ids: list[str] | None = None,
    user_ids: list[int] | None = None,
    task_ids: list[str] | None = None,
    is_billable: bool | None = None,
    include_fixed_fee: bool = True,
) -> list[dict[str, Any]]:

    rows = await get_time_report_all_rows(
        session,
        group_by=group_by,
        date_from=date_from,
        date_to=date_to,
        client_ids=client_ids,
        project_ids=project_ids,
        user_ids=user_ids,
        task_ids=task_ids,
        is_billable=is_billable,
        include_fixed_fee=include_fixed_fee,
    )
    if not rows:
        return []
    return [_row_time_report_summary_for_export(r, group_by=group_by) for r in rows]
