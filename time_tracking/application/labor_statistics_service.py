from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.labor_statistics_catalog import (
    PROJECT_STATUS_CATALOG,
    WORK_TYPE_CATALOG,
    infer_work_type,
    month_period_bounds,
    project_status_for_row,
)
from application.entry_pricing import _billable_amount_for_entry
from application.package_billing import build_package_splits_index
from application.labor_statistics_scope import (
    LaborStatisticsScope,
    clamp_labor_filter_param,
    resolve_labor_statistics_scope,
)
from application.project_partner_users import list_partner_auth_user_ids_for_project
from application.project_partner_requirement import user_satisfies_partner_rule
from application.report_builder import (
    _base_entry_conditions,
    _d,
    _hours,
    _load_clients_map,
    _load_initials_map,
    _load_projects_map,
    _load_tasks_map,
    _load_user_rates,
    _load_users_map,
    _money,
)
from application.user_initials import resolve_user_initials
from application.services.reports.export_service import export_csv, export_xlsx
from application.auth_user_directory import fetch_auth_user_partner_hints_by_id
from infrastructure.models import TimeEntryModel
from infrastructure.models_invoices import InvoiceModel, InvoicePaymentModel
from infrastructure.repositories import TeamRepository, TimeTrackingUserRepository, UserProjectAccessRepository

_ZERO = Decimal(0)
_SORT_KEYS = frozenset({
    "partner_name",
    "team_name",
    "lawyer_name",
    "client_name",
    "project_name",
    "task_name",
    "work_type",
    "period_label",
    "hours",
    "payment",
    "billable_amount",
    "rate",
})


@dataclass
class LaborStatisticsQuery:
    date_from: date
    date_to: date
    partner_id: str | None = None
    team_id: str | None = None
    client_id: str | None = None
    project_id: str | None = None
    work_type_id: str | None = None
    lawyer_id: str | None = None
    project_status_id: str | None = None
    active_projects_only: bool = False
    q: str | None = None
    sort: str = "hours"
    sort_dir: str = "desc"
    page: int = 1
    per_page: int = 50


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None or not str(raw).strip():
        return None
    try:
        n = int(str(raw).strip())
    except ValueError:
        return None
    return n if n > 0 else None


def _user_display(row) -> str:
    if row is None:
        return "—"
    name = (getattr(row, "display_name", None) or "").strip()
    if name:
        return name
    return (getattr(row, "email", None) or "").strip() or "—"


async def _lawyer_team_map(session: AsyncSession) -> dict[int, tuple[str, str, int]]:
    repo = TeamRepository(session)
    teams = await repo.list_all(include_archived=False)
    members_map = await repo.list_members_by_team_ids([t.id for t in teams])
    out: dict[int, tuple[str, str, int]] = {}
    for team in sorted(teams, key=lambda t: (t.name or "").casefold()):
        for uid in members_map.get(team.id, []):
            if uid not in out:
                out[uid] = (team.id, team.name, int(team.partner_auth_user_id))
    return out


async def _team_member_ids(session: AsyncSession, team_id: str) -> set[int]:
    repo = TeamRepository(session)
    return set(await repo.list_member_auth_user_ids(team_id))


async def _team_led_member_ids(
    session: AsyncSession,
    scope: LaborStatisticsScope,
) -> set[int] | None:
    """For partner/team-leader scope: members of teams led by the viewer (incl. self)."""
    if scope.mode != "partner" or scope.auth_user_id is None:
        return None
    repo = TeamRepository(session)
    return set(await repo.list_member_auth_user_ids_for_partner(scope.auth_user_id))


async def _load_project_payments(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    project_ids: set[str] | None,
) -> dict[str, tuple[Decimal, str]]:
    q = (
        select(
            InvoiceModel.project_id,
            InvoiceModel.currency,
            func.coalesce(func.sum(InvoicePaymentModel.amount), 0),
        )
        .join(InvoicePaymentModel, InvoicePaymentModel.invoice_id == InvoiceModel.id)
        .where(
            InvoiceModel.canceled_at.is_(None),
            InvoiceModel.project_id.isnot(None),
            func.date(InvoicePaymentModel.paid_at) >= date_from,
            func.date(InvoicePaymentModel.paid_at) <= date_to,
        )
        .group_by(InvoiceModel.project_id, InvoiceModel.currency)
    )
    if project_ids is not None:
        if not project_ids:
            return {}
        q = q.where(InvoiceModel.project_id.in_(project_ids))
    rows = (await session.execute(q)).all()
    out: dict[str, tuple[Decimal, str]] = {}
    for pid, cur, amt in rows:
        if not pid:
            continue
        key = str(pid)
        amount = _d(amt)
        if amount <= _ZERO:
            continue
        prev = out.get(key)
        if prev is None or amount > prev[0]:
            out[key] = (amount, (cur or "USD").strip() or "USD")
    return out


async def _load_entries(
    session: AsyncSession,
    q: LaborStatisticsQuery,
    scope: LaborStatisticsScope,
) -> list[TimeEntryModel]:
    user_ids: list[int] | None = None
    lawyer_filter = _parse_optional_int(q.lawyer_id)
    if scope.mode == "lawyer" and scope.auth_user_id is not None:
        user_ids = [scope.auth_user_id]
    elif lawyer_filter is not None:
        user_ids = [lawyer_filter]

    led_members = await _team_led_member_ids(session, scope)
    if led_members is not None:
        if user_ids is None:
            user_ids = sorted(led_members)
        else:
            user_ids = [u for u in user_ids if u in led_members]
        if not user_ids:
            return []

    project_ids: list[str] | None = None
    if q.project_id and q.project_id.strip():
        project_ids = [q.project_id.strip()]

    client_ids = [q.client_id.strip()] if q.client_id and q.client_id.strip() else None

    if q.team_id and q.team_id.strip():
        team_members = await _team_member_ids(session, q.team_id.strip())
        if user_ids is None:
            user_ids = sorted(team_members)
        else:
            user_ids = [u for u in user_ids if u in team_members]
        if not user_ids:
            return []

    cond = _base_entry_conditions(
        q.date_from,
        q.date_to,
        user_ids,
        project_ids,
        client_ids,
        include_fixed_fee=True,
    )
    r = await session.execute(
        select(TimeEntryModel).where(*cond).order_by(
            TimeEntryModel.work_date.asc(),
            TimeEntryModel.auth_user_id.asc(),
        )
    )
    return list(r.scalars().all())


def _row_rate(payment: float, billable_hours: float) -> float:
    return payment / billable_hours if billable_hours > 0 else 0.0


def _sort_rows(rows: list[dict[str, Any]], sort: str, sort_dir: str) -> list[dict[str, Any]]:
    key = sort if sort in _SORT_KEYS else "hours"
    reverse = (sort_dir or "desc").lower() != "asc"

    def sort_val(row: dict[str, Any]) -> Any:
        if key == "rate":
            return _row_rate(float(row.get("payment") or 0), float(row.get("billable_hours") or 0))
        if key in ("hours", "payment", "billable_amount"):
            return float(row.get(key) or 0)
        if key == "period_label":
            return row.get("period_from") or ""
        return str(row.get(key) or "")

    return sorted(rows, key=sort_val, reverse=reverse)


def _filter_q(rows: list[dict[str, Any]], q: str | None) -> list[dict[str, Any]]:
    needle = (q or "").strip().casefold()
    if not needle:
        return rows
    out: list[dict[str, Any]] = []
    for row in rows:
        hay = " ".join(
            str(row.get(k) or "")
            for k in (
                "partner_name",
                "team_name",
                "lawyer_name",
                "client_name",
                "project_name",
                "task_name",
                "work_type",
                "period_label",
            )
        ).casefold()
        if needle in hay:
            out.append(row)
    return out


def _build_charts(
    rows: list[dict[str, Any]],
    daily: list[dict[str, Any]],
) -> dict[str, Any]:
    def stacked_by(label_key: str, id_key: str | None = None) -> list[dict[str, Any]]:
        acc: dict[str, dict[str, Any]] = {}
        for row in rows:
            label = str(row.get(label_key) or "—")
            rid = str(row.get(id_key) or "") if id_key else ""
            key = rid or label
            slot = acc.get(key)
            if slot is None:
                slot = {"id": rid or label, "name": label, "billable_hours": 0.0, "non_billable_hours": 0.0}
                acc[key] = slot
            h = float(row.get("hours") or 0)
            bh = float(row.get("billable_hours") or 0)
            slot["billable_hours"] += bh
            slot["non_billable_hours"] += max(0.0, h - bh)
        items = sorted(
            acc.values(),
            key=lambda x: float(x["billable_hours"]) + float(x["non_billable_hours"]),
            reverse=True,
        )[:12]
        return [
            {
                "id": str(item["id"]),
                "name": str(item["name"]),
                "billable_hours": round(float(item["billable_hours"]), 2),
                "non_billable_hours": round(float(item["non_billable_hours"]), 2),
            }
            for item in items
        ]

    def pie_by(label_key: str, hours_key: str = "hours") -> list[dict[str, Any]]:
        acc: dict[str, float] = defaultdict(float)
        for row in rows:
            label = str(row.get(label_key) or "—")
            acc[label] += float(row.get(hours_key) or 0)
        items = sorted(acc.items(), key=lambda x: x[1], reverse=True)[:10]
        total = sum(v for _, v in items) or 1.0
        return [
            {"name": name, "value": round(v / total * 100, 1), "hours": round(v, 2)}
            for name, v in items
        ]

    client_pay: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = str(row.get("client_id") or "")
        if not cid:
            continue
        slot = client_pay.setdefault(
            cid,
            {
                "name": row.get("client_name") or "—",
                "hours": 0.0,
                "payment": 0.0,
                "billable_amount": 0.0,
                "currency": row.get("currency") or "USD",
            },
        )
        slot["hours"] += float(row.get("hours") or 0)
        slot["payment"] += float(row.get("payment") or 0)
        slot["billable_amount"] += float(row.get("billable_amount") or 0)

    hours_vs_payment = [
        {
            "name": v["name"],
            "hours": round(v["hours"], 2),
            "payment": round(v["payment"], 2),
            "billable_amount": round(v["billable_amount"], 2),
            "currency": v["currency"],
        }
        for v in sorted(client_pay.values(), key=lambda x: x["payment"], reverse=True)[:12]
    ]

    efficiency = []
    for v in client_pay.values():
        h = float(v["hours"] or 0)
        pay = float(v["payment"] or 0)
        if h <= 0:
            continue
        efficiency.append({
            "name": v["name"],
            "hours": round(h, 2),
            "payment": round(pay, 2),
            "billable_amount": round(float(v["billable_amount"] or 0), 2),
            "rate_per_hour": round(pay / h, 2),
            "currency": v["currency"],
        })
    efficiency.sort(key=lambda x: x["rate_per_hour"], reverse=True)

    work_type_rows = []
    acc: dict[str, float] = defaultdict(float)
    for row in rows:
        acc[str(row.get("work_type") or "Иное")] += float(row.get("hours") or 0)
    wt_total = sum(acc.values()) or 1.0
    for name, hrs in sorted(acc.items(), key=lambda x: x[1], reverse=True):
        work_type_rows.append({
            "name": name,
            "value": round(hrs / wt_total * 100, 1),
            "hours": round(hrs, 2),
        })

    team_finance: dict[str, dict[str, Any]] = {}
    for row in rows:
        tid = str(row.get("team_id") or "") or "_none"
        slot = team_finance.setdefault(
            tid,
            {
                "team_id": str(row.get("team_id") or ""),
                "team_name": str(row.get("team_name") or "—") or "—",
                "hours": 0.0,
                "billable_hours": 0.0,
                "billable_amount": 0.0,
                "paid_amount": 0.0,
                "currency": row.get("currency") or "USD",
            },
        )
        slot["hours"] += float(row.get("hours") or 0)
        slot["billable_hours"] += float(row.get("billable_hours") or 0)
        slot["billable_amount"] += float(row.get("billable_amount") or 0)
        slot["paid_amount"] += float(row.get("payment") or 0)
        if float(row.get("payment") or 0) > 0 or float(row.get("billable_amount") or 0) > 0:
            slot["currency"] = row.get("currency") or slot["currency"]

    by_teams_finance = [
        {
            "team_id": v["team_id"],
            "team_name": v["team_name"],
            "hours": round(v["hours"], 2),
            "billable_hours": round(v["billable_hours"], 2),
            "billable_amount": round(v["billable_amount"], 2),
            "paid_amount": round(v["paid_amount"], 2),
            "currency": v["currency"],
        }
        for v in sorted(team_finance.values(), key=lambda x: x["billable_amount"], reverse=True)
    ]

    return {
        "hours_by_day": daily,
        "by_users": stacked_by("lawyer_name", "lawyer_id"),
        "by_projects": stacked_by("project_name", "project_id"),
        "by_clients": stacked_by("client_name", "client_id"),
        "by_project_status": stacked_by("project_status", "project_status_id"),
        "by_teams": stacked_by("team_name", "team_id"),
        "by_work_type": work_type_rows,
        "hours_by_project_ranking": pie_by("project_name"),
        "hours_by_task": pie_by("task_name"),
        "hours_vs_payment": hours_vs_payment,
        "payment_efficiency_ranking": efficiency[:12],
        "by_teams_finance": by_teams_finance,
    }


def _build_kpi(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_hours = sum(float(r.get("hours") or 0) for r in rows)
    billable_hours = sum(float(r.get("billable_hours") or 0) for r in rows)
    paid_amount = sum(float(r.get("payment") or 0) for r in rows)
    billable_amount = sum(float(r.get("billable_amount") or 0) for r in rows)
    paid_currency = next(
        (str(r.get("currency") or "USD") for r in rows if float(r.get("payment") or 0) > 0),
        next(
            (str(r.get("currency") or "USD") for r in rows if float(r.get("billable_amount") or 0) > 0),
            "USD",
        ),
    )
    return {
        "total_hours": round(total_hours, 2),
        "billable_hours": round(billable_hours, 2),
        "non_billable_hours": round(max(0.0, total_hours - billable_hours), 2),
        "paid_amount": round(paid_amount, 2),
        "paid_currency": paid_currency,
        "rate_per_hour": round(paid_amount / billable_hours, 2) if billable_hours > 0 else 0.0,
        "billable_amount": round(billable_amount, 2),
        "billable_currency": paid_currency,
        "accrued_rate_per_hour": round(billable_amount / billable_hours, 2) if billable_hours > 0 else 0.0,
    }


async def build_labor_statistics(
    session: AsyncSession,
    viewer: dict,
    q: LaborStatisticsQuery,
    *,
    authorization: str | None = None,
) -> dict[str, Any]:
    if q.date_to < q.date_from:
        return {
            "kpi": _build_kpi([]),
            "detail": {"rows": [], "total": 0, "page": q.page, "per_page": q.per_page},
            "charts": _build_charts([], []),
        }

    scope = resolve_labor_statistics_scope(viewer)
    partner_id, lawyer_id = clamp_labor_filter_param(
        scope,
        partner_id=q.partner_id,
        lawyer_id=q.lawyer_id,
    )
    q = LaborStatisticsQuery(
        **{
            **q.__dict__,
            "partner_id": partner_id,
            "lawyer_id": lawyer_id,
        }
    )

    entries = await _load_entries(session, q, scope)
    if not entries:
        return {
            "kpi": _build_kpi([]),
            "detail": {"rows": [], "total": 0, "page": q.page, "per_page": q.per_page},
            "charts": _build_charts([], []),
        }

    project_ids = {str(e.project_id) for e in entries if e.project_id}
    entry_user_ids = sorted({int(e.auth_user_id) for e in entries})
    projects_map = await _load_projects_map(session)
    clients_map = await _load_clients_map(session)
    tasks_map = await _load_tasks_map(session)
    users_map = await _load_users_map(session)
    initials_map = await _load_initials_map(session)
    rates_map = await _load_user_rates(session, entry_user_ids)

    package_splits, package_months_by_project = build_package_splits_index(
        projects_map,
        entries,
        date_from=q.date_from,
        date_to=q.date_to,
    )
    package_fees_total = 0.0
    for pid, summaries in package_months_by_project.items():
        p = projects_map.get(pid)
        if not p:
            continue
        for s in summaries:
            package_fees_total += float(s.package_fee)

    access_repo = UserProjectAccessRepository(session)
    partners_by_project: dict[str, list[int]] = {}
    for pid in project_ids:
        partners_by_project[pid] = await list_partner_auth_user_ids_for_project(
            session, access_repo, pid, authorization=authorization
        )

    lawyer_team = await _lawyer_team_map(session)
    payments_by_project = await _load_project_payments(
        session, q.date_from, q.date_to, project_ids
    )

    today = date.today()
    buckets: dict[tuple, dict[str, Any]] = {}
    daily_acc: dict[str, dict[str, float]] = defaultdict(lambda: {"billable": 0.0, "total": 0.0})

    for e in entries:
        p = projects_map.get(e.project_id) if e.project_id else None
        if p is None:
            continue
        if q.active_projects_only and (p.is_archived or (p.end_date and p.end_date < today)):
            continue
        status_id, status_label = project_status_for_row(
            is_archived=bool(p.is_archived),
            end_date=p.end_date,
            today=today,
        )
        if q.project_status_id and q.project_status_id.strip() and status_id != q.project_status_id.strip():
            continue

        c = clients_map.get(p.client_id) if p.client_id else None
        t = tasks_map.get(e.task_id) if e.task_id else None
        u = users_map.get(e.auth_user_id)
        wt_id, wt_name = infer_work_type(t.name if t else None)
        if q.work_type_id and q.work_type_id.strip() and wt_id != q.work_type_id.strip():
            continue

        partner_ids = partners_by_project.get(str(p.id), [])
        partner_uid = partner_ids[0] if partner_ids else None
        partner_user = users_map.get(partner_uid) if partner_uid else None
        if q.partner_id and q.partner_id.strip():
            if str(partner_uid or "") != q.partner_id.strip():
                continue

        team_id, team_name, _ = lawyer_team.get(e.auth_user_id, ("", "", 0))
        period_from, period_to, period_label = month_period_bounds(e.work_date)
        key = (e.auth_user_id, p.client_id or "", p.id, e.task_id or "", period_from.isoformat())
        slot = buckets.get(key)
        h = _d(e.hours)
        bh = h if e.is_billable else _ZERO
        pc = (p.currency or "USD").strip() or "USD"
        split = package_splits.get(str(e.id))
        if split is not None:
            oh = _d(split.overage_hours)
            amt, amt_cur = _billable_amount_for_entry(
                oh,
                bool(e.is_billable) and oh > 0,
                e.work_date,
                rates_map.get(e.auth_user_id),
                project_currency=pc,
                time_entry_project_id=e.project_id,
            )
        else:
            amt, amt_cur = _billable_amount_for_entry(
                h,
                bool(e.is_billable),
                e.work_date,
                rates_map.get(e.auth_user_id),
                project_currency=pc,
                time_entry_project_id=e.project_id,
            )
        if slot is None:
            slot = {
                "id": str(uuid.uuid4()),
                "partner_id": str(partner_uid or ""),
                "partner_name": _user_display(partner_user),
                "partner_initials": resolve_user_initials(partner_user, initials_map=initials_map),
                "team_id": team_id,
                "team_name": team_name or "",
                "lawyer_id": str(e.auth_user_id),
                "lawyer_name": _user_display(u),
                "lawyer_initials": resolve_user_initials(u, initials_map=initials_map),
                "client_id": p.client_id or "",
                "client_name": c.name if c else "—",
                "project_id": p.id,
                "project_name": p.name,
                "project_active": not p.is_archived and not (p.end_date and p.end_date < today),
                "project_status_id": status_id,
                "project_status": status_label,
                "task_id": e.task_id or "",
                "task_name": t.name if t else "—",
                "work_type_id": wt_id,
                "work_type": wt_name,
                "period_from": period_from.isoformat(),
                "period_to": period_to.isoformat(),
                "period_label": period_label,
                "hours": 0.0,
                "billable_hours": 0.0,
                "billable_amount": 0.0,
                "payment": 0.0,
                "currency": pc,
            }
            buckets[key] = slot
        slot["hours"] = float(slot["hours"]) + _hours(h)
        slot["billable_hours"] = float(slot["billable_hours"]) + _hours(bh)
        slot["billable_amount"] = float(slot["billable_amount"]) + float(_money(amt))
        if amt > _ZERO:
            slot["currency"] = (amt_cur or pc).strip() or pc

        dkey = e.work_date.isoformat()
        daily_acc[dkey]["total"] += _hours(h)
        if e.is_billable:
            daily_acc[dkey]["billable"] += _hours(h)

    project_billable: dict[str, float] = defaultdict(float)
    for slot in buckets.values():
        pid = str(slot.get("project_id") or "")
        project_billable[pid] += float(slot.get("billable_hours") or 0)

    for slot in buckets.values():
        pid = str(slot.get("project_id") or "")
        pay_info = payments_by_project.get(pid)
        if not pay_info:
            continue
        pay_amt, pay_cur = pay_info
        denom = project_billable.get(pid) or 0.0
        if denom <= 0:
            continue
        share = float(slot.get("billable_hours") or 0) / denom
        slot["payment"] = round(float(_money(pay_amt)) * share, 2)
        slot["currency"] = pay_cur

    for slot in buckets.values():
        slot["billable_amount"] = round(float(slot.get("billable_amount") or 0), 2)

    all_rows = list(buckets.values())
    kpi = _build_kpi(all_rows)
    if package_fees_total:
        kpi["package_fees"] = round(package_fees_total, 2)
        kpi["billable_amount"] = round(float(kpi.get("billable_amount") or 0) + package_fees_total, 2)
        if float(kpi.get("billable_hours") or 0) > 0:
            kpi["accrued_rate_per_hour"] = round(
                float(kpi["billable_amount"]) / float(kpi["billable_hours"]), 2
            )

    daily = []
    for dkey in sorted(daily_acc.keys()):
        vals = daily_acc[dkey]
        daily.append({
            "date": dkey,
            "date_label": dkey,
            "billable_hours": round(vals["billable"], 2),
            "total_hours": round(vals["total"], 2),
        })

    charts = _build_charts(all_rows, daily)

    filtered = _filter_q(all_rows, q.q)
    sorted_rows = _sort_rows(filtered, q.sort, q.sort_dir)
    per_page = min(max(q.per_page, 1), 200)
    page = max(q.page, 1)
    start = (page - 1) * per_page
    page_rows = sorted_rows[start : start + per_page]

    return {
        "kpi": kpi,
        "detail": {
            "rows": page_rows,
            "total": len(filtered),
            "page": page,
            "per_page": per_page,
        },
        "charts": charts,
    }


async def build_labor_statistics_meta(
    session: AsyncSession,
    viewer: dict,
    *,
    authorization: str | None = None,
) -> dict[str, Any]:
    scope = resolve_labor_statistics_scope(viewer)
    users_repo = TimeTrackingUserRepository(session)
    users = await users_repo.list_users()
    initials_map = await _load_initials_map(session)
    hints = await fetch_auth_user_partner_hints_by_id(authorization or "")

    partners: list[dict[str, str]] = []
    for u in users:
        if u.is_archived or u.is_blocked:
            continue
        hint = hints.get(u.auth_user_id) or {}
        label = _user_display(u)
        initials = resolve_user_initials(u, initials_map=initials_map)
        if user_satisfies_partner_rule(u.position, hint.get("position"), hint.get("role")):
            partners.append({"id": str(u.auth_user_id), "name": label, "initials": initials})

    teams_repo = TeamRepository(session)
    teams = await teams_repo.list_all(include_archived=False)
    if scope.mode == "partner" and scope.auth_user_id is not None:
        teams = [t for t in teams if int(t.partner_auth_user_id) == int(scope.auth_user_id)]
    team_rows = [{"id": t.id, "name": t.name} for t in teams]

    led_member_ids: set[int] | None = None
    if scope.mode == "partner" and scope.auth_user_id is not None:
        led_member_ids = set(await teams_repo.list_member_auth_user_ids_for_partner(scope.auth_user_id))

    projects_map = await _load_projects_map(session)
    clients_map = await _load_clients_map(session)

    clients_out: list[dict[str, str]] = []
    projects_out: list[dict[str, str]] = []
    for p in projects_map.values():
        if p.is_archived:
            continue
        c = clients_map.get(p.client_id)
        if c and not any(x["id"] == c.id for x in clients_out):
            clients_out.append({"id": c.id, "name": c.name})
        projects_out.append({
            "id": p.id,
            "name": p.name,
            "client_id": p.client_id or "",
        })

    lawyers: list[dict[str, str]] = []
    for u in users:
        if u.is_archived or u.is_blocked:
            continue
        if scope.mode == "lawyer" and scope.auth_user_id != u.auth_user_id:
            continue
        if scope.mode == "partner" and led_member_ids is not None and u.auth_user_id not in led_member_ids:
            continue
        lawyers.append({
            "id": str(u.auth_user_id),
            "name": _user_display(u),
            "email": u.email,
            "initials": resolve_user_initials(u, initials_map=initials_map),
        })

    clients_out.sort(key=lambda x: x["name"].casefold())
    projects_out.sort(key=lambda x: x["name"].casefold())
    partners.sort(key=lambda x: x["name"].casefold())
    lawyers.sort(key=lambda x: x["name"].casefold())
    team_rows.sort(key=lambda x: x["name"].casefold())

    return {
        "partners": partners,
        "teams": team_rows,
        "clients": clients_out,
        "projects": projects_out,
        "work_types": [{"id": wid, "name": wname} for wid, wname in WORK_TYPE_CATALOG],
        "lawyers": lawyers,
        "project_statuses": [{"id": sid, "name": slabel} for sid, slabel in PROJECT_STATUS_CATALOG],
    }


async def export_labor_statistics(
    session: AsyncSession,
    viewer: dict,
    q: LaborStatisticsQuery,
    *,
    export_format: str,
    authorization: str | None = None,
):
    probe = await build_labor_statistics(
        session,
        viewer,
        LaborStatisticsQuery(**{**q.__dict__, "page": 1, "per_page": 1}),
        authorization=authorization,
    )
    total = int(probe["detail"]["total"])
    big_q = LaborStatisticsQuery(**{**q.__dict__, "page": 1, "per_page": min(max(total, 1), 10000)})
    data = await build_labor_statistics(session, viewer, big_q, authorization=authorization)
    rows = data["detail"]["rows"]

    export_rows: list[dict[str, Any]] = []
    for row in rows:
        export_rows.append({
            "Партнёр": row.get("partner_name"),
            "Инициалы партнёра": row.get("partner_initials"),
            "Команда": row.get("team_name"),
            "Юрист": row.get("lawyer_name"),
            "Инициалы юриста": row.get("lawyer_initials"),
            "Клиент": row.get("client_name"),
            "Проект": row.get("project_name"),
            "Задача": row.get("task_name"),
            "Тип работы": row.get("work_type"),
            "Период": row.get("period_label"),
            "Часы": row.get("hours"),
            "Начислено": row.get("billable_amount"),
            "Оплата": row.get("payment"),
            "Оплата за час": _row_rate(float(row.get("payment") or 0), float(row.get("billable_hours") or 0)),
        })

    fmt = (export_format or "csv").strip().lower()
    if fmt == "xlsx":
        return export_xlsx(export_rows, "labor", None, q.date_from, q.date_to)
    return export_csv(export_rows, "labor", None, q.date_from, q.date_to)
