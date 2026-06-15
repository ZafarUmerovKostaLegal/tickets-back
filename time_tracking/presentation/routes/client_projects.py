

import csv
import json
from datetime import date
from decimal import Decimal
from io import StringIO
from typing import Literal

from application.auth_user_directory import ensure_time_tracking_user_from_auth
from application.budget_mode import (
    budget_limit_hours,
    budget_limit_money,
    budget_mode,
    normalize_budget_type_for_persist,
)
from application.client_task_defaults import seed_default_common_tasks_for_project
from application.entry_pricing import _billable_amount_for_entry
from application.project_access_rates import validate_hourly_rates_for_project_access
from application.project_billable_rate_sync import (
    project_uses_shared_billable,
    reapply_project_billable_mode,
    sync_project_billable_rates_to_assigned_users,
    upsert_user_project_scoped_billable_rate,
)
from application.project_dashboard import build_client_project_dashboard
from application.project_partner_index import build_project_partner_participant_index
from application.project_partner_requirement import ensure_projects_have_partner_assignee
from application.report_builder import _load_user_rates
from application.services.reports._base import _ZERO, _d, _hours, _money
from application.access_control import ensure_can_list_project_assignees
from application.project_team_workload import compute_project_team_workload
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from infrastructure.database import get_session
from infrastructure.models import TimeEntryModel
from infrastructure.repositories import (
    ClientProjectRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)
from presentation.deps import require_bearer_user
from presentation.routes.client_access import ensure_client_not_archived, get_client_or_404
from presentation.schemas import (
    ProjectType,
    TeamWorkloadOut,
    TimeManagerClientProjectCodeHintOut,
    TimeManagerClientProjectCreateBody,
    TimeManagerClientProjectOut,
    TimeManagerClientProjectPatchBody,
    ProjectTimeTrackingAssigneesListOut,
    ProjectTimeTrackingAssigneeOut,
)

router = APIRouter(prefix="/clients", tags=["client_projects"])


def _positive_budget_amount(v) -> bool:
    return v is not None and _d(v) > 0


def _out_fixed_fee_amount_for_api(row) -> object:

    if getattr(row, "project_type", None) != "fixed_fee":
        return row.fixed_fee_amount
    if _positive_budget_amount(getattr(row, "budget_amount", None)):
        return row.budget_amount
    return row.fixed_fee_amount


_global_projects_router = APIRouter(tags=["projects_global"])


@_global_projects_router.get("/projects-for-expenses")
async def list_all_projects_for_expenses(
    include_archived: bool = Query(False, alias="includeArchived"),
    limit: int | None = Query(None, ge=1, le=500, description="Если задано — пагинированный ответ"),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):

    repo = ClientProjectRepository(session)
    from infrastructure.repositories import ClientRepository

    cr = ClientRepository(session)
    if limit is None:
        clients = {c.id: c for c in await cr.list_all(include_archived=True)}
        rows = await repo.list_all_global(include_archived=include_archived)
    else:
        rows, total = await repo.list_all_global_paginated(
            include_archived=include_archived, limit=limit, offset=offset
        )
        cids = {r.client_id for r in rows}
        clients = await cr.get_by_ids(cids)
    items = [
        {
            "id": r.id,
            "name": r.name,
            "code": r.code,
            "clientId": r.client_id,
            "clientName": clients[r.client_id].name if r.client_id in clients else None,
            "isArchived": r.is_archived,
        }
        for r in rows
    ]
    if limit is None:
        return items
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@_global_projects_router.get("/projects")
async def list_all_client_projects(
    include_archived: bool = Query(False, alias="includeArchived"),
    include_budget_metrics: bool = Query(
        False,
        alias="includeBudgetMetrics",
        description="Тяжёлый расчёт бюджета по всем списаниям; для списка лучше false + /projects/budget-metrics",
    ),
    limit: int | None = Query(None, ge=1, le=500, description="Если задано — пагинированный ответ"),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(None, alias="Authorization"),
):
    """Все проекты (полные карточки) одним запросом — для списка проектов на фронте."""
    repo = ClientProjectRepository(session)
    if limit is None:
        rows = await repo.list_all_global(include_archived=include_archived)
    else:
        rows, total = await repo.list_all_global_paginated(
            include_archived=include_archived, limit=limit, offset=offset
        )
    out = await _client_projects_to_out(
        session,
        repo,
        rows,
        include_budget_metrics=include_budget_metrics,
        authorization=authorization,
    )
    if limit is None:
        return out
    return {"items": out, "total": total, "limit": limit, "offset": offset}


@_global_projects_router.get("/projects/budget-metrics")
async def get_projects_budget_metrics(
    ids: str = Query(..., description="ID проектов через запятую (до 80)"),
    session: AsyncSession = Depends(get_session),
):
    id_list = [x.strip() for x in ids.split(",") if x.strip()][:80]
    if not id_list:
        return {}
    repo = ClientProjectRepository(session)
    rows = await repo.list_by_ids_global(id_list)
    budget_map = await _project_budget_metrics(session, rows)
    return _budget_metrics_payload(budget_map)


@_global_projects_router.get(
    "/projects/{project_id}/time-tracking-assignees",
    response_model=ProjectTimeTrackingAssigneesListOut,
    summary="Сотрудники с доступом к проекту (селектор при добавлении списаний)",
)
async def list_time_tracking_assignees_for_project(
    project_id: str,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> ProjectTimeTrackingAssigneesListOut:

    await ensure_can_list_project_assignees(session, viewer, project_id)
    par = UserProjectAccessRepository(session)
    uids = await par.list_auth_user_ids_for_project(project_id.strip())
    ur = TimeTrackingUserRepository(session)
    rows = await ur.list_by_auth_user_ids(uids)
    by_uid = {r.auth_user_id: r for r in rows}

    def _label_lower(uid: int) -> str:
        u = by_uid.get(uid)
        if u is None:
            return str(uid)
        return (u.display_name or u.email or str(uid)).lower()

    items: list[ProjectTimeTrackingAssigneeOut] = []
    for uid in sorted(set(uids), key=_label_lower):
        u = by_uid.get(uid)
        if u is None:
            continue
        items.append(
            ProjectTimeTrackingAssigneeOut(
                auth_user_id=u.auth_user_id,
                display_name=u.display_name,
                email=u.email,
                position=(u.position or "").strip() or None,
                is_archived=bool(u.is_archived),
                is_blocked=bool(u.is_blocked),
            )
        )
    return ProjectTimeTrackingAssigneesListOut(assignees=items)


@_global_projects_router.get("/projects/{project_id}/expense-categories")
async def list_expense_categories_for_project(
    project_id: str,
    include_archived: bool = Query(False, alias="includeArchived"),
    session: AsyncSession = Depends(get_session),
):

    repo = ClientProjectRepository(session)
    row = await repo.get_by_id_global(project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    from infrastructure.repositories import ClientExpenseCategoryRepository

    ec_repo = ClientExpenseCategoryRepository(session)
    cats = await ec_repo.list_for_client(row.client_id, include_archived=include_archived)
    return [
        {
            "id": c.id,
            "name": c.name,
            "hasUnitPrice": c.has_unit_price,
            "isArchived": c.is_archived,
        }
        for c in cats
    ]


def _parse_dashboard_date(param: str | None) -> date | None:
    if param is None or not str(param).strip():
        return None
    s = str(param).strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Некорректная дата: {param!r}") from e


def _suggest_next_code(last: str | None) -> str | None:
    if not last or not str(last).strip():
        return None
    s = str(last).strip()
    i = s.rfind("-")
    if i <= 0:
        return None
    prefix, suffix = s[:i], s[i + 1 :]
    if not suffix.isdigit():
        return None
    n = int(suffix)
    width = len(suffix)
    return f"{prefix}-{str(n + 1).zfill(width)}"


def _project_out(row, usage: int) -> TimeManagerClientProjectOut:
    return TimeManagerClientProjectOut(
        id=row.id,
        client_id=row.client_id,
        name=row.name,
        code=row.code,
        start_date=row.start_date,
        end_date=row.end_date,
        notes=row.notes,
        report_visibility=row.report_visibility,
        project_type=row.project_type,
        currency=getattr(row, "currency", "USD") or "USD",
        billable_rate_type=row.billable_rate_type,
        project_billable_rate_amount=row.project_billable_rate_amount,
        budget_type=row.budget_type,
        budget_amount=row.budget_amount,
        progress_budget_amount=row.progress_budget_amount,
        budget_hours=row.budget_hours,
        budget_resets_every_month=row.budget_resets_every_month,
        budget_includes_expenses=row.budget_includes_expenses,
        send_budget_alerts=row.send_budget_alerts,
        budget_alert_threshold_percent=row.budget_alert_threshold_percent,
        fixed_fee_amount=_out_fixed_fee_amount_for_api(row),
        is_archived=row.is_archived,
        created_at=row.created_at,
        updated_at=row.updated_at,
        usage_count=usage,
        deletable=usage == 0,
    )


def _progress_percent(spent, limit) -> float:
    if limit <= _ZERO:
        return 0.0
    return round(float((spent / limit) * 100), 2)


async def _project_budget_metrics(
    session: AsyncSession,
    rows: list,
) -> dict[str, dict[str, float | bool]]:
    pids = [str(r.id) for r in rows]
    if not pids:
        return {}
    q = select(TimeEntryModel).where(
        TimeEntryModel.project_id.in_(pids),
        TimeEntryModel.voided_at.is_(None),
    )
    entries = list((await session.execute(q)).scalars().all())
    user_ids = list({int(e.auth_user_id) for e in entries})
    rates_map = await _load_user_rates(session, user_ids or None)
    spent_hours: dict[str, Decimal] = {}
    spent_money: dict[str, Decimal] = {}
    projects_by_id = {str(r.id): r for r in rows}
    for e in entries:
        pid = (e.project_id or "").strip()
        if not pid:
            continue
        h = _d(e.hours)
        spent_hours[pid] = spent_hours.get(pid, _ZERO) + h
        if not e.is_billable:
            continue
        p = projects_by_id.get(pid)
        cur = (getattr(p, "currency", None) or "USD") if p else "USD"
        amt, _ = _billable_amount_for_entry(
            h,
            e.is_billable,
            e.work_date,
            rates_map.get(e.auth_user_id),
            project_currency=cur,
            time_entry_project_id=pid,
        )
        spent_money[pid] = spent_money.get(pid, _ZERO) + amt
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        pid = str(r.id)
        mode = budget_mode(r)
        lim_h = budget_limit_hours(r)
        lim_m = budget_limit_money(r)
        sh = spent_hours.get(pid, _ZERO)
        sm = spent_money.get(pid, _ZERO)
        rh = max(_ZERO, lim_h - sh) if lim_h > _ZERO else _ZERO
        rm = max(_ZERO, lim_m - sm) if lim_m > _ZERO else _ZERO
        if mode == "hours":
            out[pid] = {
                "budget_display_value": _hours(lim_h),
                "budget_spent_value": _hours(sh),
                "budget_remaining_value": _hours(rh),
                "budget_progress_percent": _progress_percent(sh, lim_h),
                "logged_hours_value": _hours(sh),
                "has_budget_configured": lim_h > _ZERO,
            }
        elif mode in ("money", "hours_and_money"):
            out[pid] = {
                "budget_display_value": _money(lim_m),
                "budget_spent_value": _money(sm),
                "budget_remaining_value": _money(rm),
                "budget_progress_percent": _progress_percent(sm, lim_m),
                "logged_hours_value": _hours(sh),
                "has_budget_configured": lim_m > _ZERO or lim_h > _ZERO,
            }
        else:
            out[pid] = {
                "budget_display_value": 0.0,
                "budget_spent_value": 0.0,
                "budget_remaining_value": 0.0,
                "budget_progress_percent": 0.0,
                "logged_hours_value": _hours(sh),
                "has_budget_configured": False,
            }
    return out


async def _client_projects_to_out(
    session: AsyncSession,
    repo: ClientProjectRepository,
    rows: list,
    *,
    include_budget_metrics: bool = True,
    authorization: str | None = None,
) -> list[TimeManagerClientProjectOut]:
    pids = [r.id for r in rows]
    usage_map = await repo.time_entries_counts_by_project_ids(pids)
    budget_map = (
        await _project_budget_metrics(session, rows)
        if include_budget_metrics
        else {}
    )
    partners_by_project, participants_by_project = await build_project_partner_participant_index(
        session,
        pids,
        authorization=authorization,
    )
    out: list[TimeManagerClientProjectOut] = []
    for r in rows:
        row_out = _project_out(r, usage_map.get(r.id, 0))
        pid = str(r.id)
        row_out.partner_auth_user_ids = partners_by_project.get(pid, [])
        row_out.participant_auth_user_ids = participants_by_project.get(pid, [])
        bm = budget_map.get(pid, {})
        row_out.budget_display_value = bm.get("budget_display_value")
        row_out.budget_spent_value = bm.get("budget_spent_value")
        row_out.budget_remaining_value = bm.get("budget_remaining_value")
        row_out.budget_progress_percent = bm.get("budget_progress_percent")
        row_out.logged_hours_value = bm.get("logged_hours_value")
        row_out.has_budget_configured = bm.get("has_budget_configured")
        out.append(row_out)
    return out


def _budget_metrics_payload(budget_map: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for pid, bm in budget_map.items():
        out[pid] = {
            "budgetDisplayValue": bm.get("budget_display_value"),
            "budgetSpentValue": bm.get("budget_spent_value"),
            "budgetRemainingValue": bm.get("budget_remaining_value"),
            "budgetProgressPercent": bm.get("budget_progress_percent"),
            "loggedHoursValue": bm.get("logged_hours_value"),
            "hasBudgetConfigured": bm.get("has_budget_configured"),
        }
    return out


def _validate_date_range(start: date | None, end: date | None) -> None:
    if start is not None and end is not None and end < start:
        raise HTTPException(
            status_code=400,
            detail="end_date must be on or after start_date",
        )


async def _require_client(session: AsyncSession, client_id: str) -> None:
    await get_client_or_404(session, client_id)


async def _require_client_mutable(session: AsyncSession, client_id: str) -> None:
    row = await get_client_or_404(session, client_id)
    ensure_client_not_archived(row)


@router.get("/{client_id}/projects/code-hint", response_model=TimeManagerClientProjectCodeHintOut)
async def get_client_project_code_hint(
    client_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client(session, client_id)
    repo = ClientProjectRepository(session)
    row = await repo.get_last_project_with_code(client_id)
    last = row.code.strip() if row and row.code else None
    return TimeManagerClientProjectCodeHintOut(
        last_code=last,
        suggested_next=_suggest_next_code(last),
    )


def _export_filename_stub(code: str | None, project_id: str) -> str:
    if code and str(code).strip():
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(code).strip()[:48])
        return safe or project_id
    return project_id


@router.post(
    "/{client_id}/projects/{project_id}/duplicate",
    response_model=TimeManagerClientProjectOut,
)
async def duplicate_client_project(
    client_id: str,
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientProjectRepository(session)
    try:
        row = await repo.duplicate_from(client_id, project_id)
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Could not duplicate project (code conflict)",
        ) from None
    await session.refresh(row)
    usage = await repo.time_entries_count(row.id)
    return _project_out(row, usage)


@router.get("/{client_id}/projects/{project_id}/export")
async def export_client_project(
    client_id: str,
    project_id: str,
    export_format: Literal["json", "csv"] = Query("json", alias="format"),
    session: AsyncSession = Depends(get_session),
):
    await _require_client(session, client_id)
    repo = ClientProjectRepository(session)
    row = await repo.get_by_id(client_id, project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    usage = await repo.time_entries_count(row.id)
    data = _project_out(row, usage).model_dump(mode="json")
    stub = _export_filename_stub(row.code, row.id)
    if export_format == "json":
        body = json.dumps(data, ensure_ascii=False, indent=2)
        return Response(
            content=body.encode("utf-8"),
            media_type="application/json; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{stub}.json"',
            },
        )
    buf = StringIO()
    w = csv.writer(buf)
    flat = {k: ("" if v is None else v) for k, v in data.items()}
    w.writerow(list(flat.keys()))
    w.writerow([str(flat[k]) for k in flat.keys()])
    csv_text = "\ufeff" + buf.getvalue()
    return Response(
        content=csv_text.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{stub}.csv"',
        },
    )


@router.get("/{client_id}/projects")
async def list_client_projects(
    client_id: str,
    include_archived: bool = Query(False, alias="includeArchived"),
    limit: int | None = Query(None, ge=1, le=500, description="Если задано — пагинированный ответ"),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(None, alias="Authorization"),
):
    await _require_client(session, client_id)
    repo = ClientProjectRepository(session)
    if limit is None:
        rows = await repo.list_for_client(client_id, include_archived=include_archived)
    else:
        rows, total = await repo.list_for_client_paginated(
            client_id, include_archived=include_archived, limit=limit, offset=offset
        )
    out = await _client_projects_to_out(
        session, repo, rows, authorization=authorization,
    )
    if limit is None:
        return out
    return {"items": out, "total": total, "limit": limit, "offset": offset}


@router.get(
    "/{client_id}/projects/{project_id}",
    response_model=TimeManagerClientProjectOut,
)
async def get_client_project(
    client_id: str,
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client(session, client_id)
    repo = ClientProjectRepository(session)
    row = await repo.get_by_id(client_id, project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    usage = await repo.time_entries_count(row.id)
    out = _project_out(row, usage)
    bm = await _project_budget_metrics(session, [row])
    one = bm.get(str(row.id), {})
    out.budget_display_value = one.get("budget_display_value")
    out.budget_spent_value = one.get("budget_spent_value")
    out.budget_remaining_value = one.get("budget_remaining_value")
    out.budget_progress_percent = one.get("budget_progress_percent")
    out.logged_hours_value = one.get("logged_hours_value")
    out.has_budget_configured = one.get("has_budget_configured")
    return out


@router.get("/{client_id}/projects/{project_id}/dashboard")
async def get_client_project_dashboard(
    client_id: str,
    project_id: str,
    session: AsyncSession = Depends(get_session),
    date_from: str | None = Query(None, description="YYYY-MM-DD"),
    date_to: str | None = Query(None, description="YYYY-MM-DD"),
):

    await _require_client(session, client_id)
    df = _parse_dashboard_date(date_from)
    dt = _parse_dashboard_date(date_to)
    try:
        payload = await build_client_project_dashboard(
            session,
            client_id=client_id,
            project_id=project_id,
            date_from=df,
            date_to=dt,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not payload:
        raise HTTPException(status_code=404, detail="Project not found")
    return payload


@router.get("/{client_id}/projects/{project_id}/team-workload", response_model=TeamWorkloadOut)
async def get_project_team_workload(
    client_id: str,
    project_id: str,
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    include_archived: bool = Query(False, alias="includeArchived"),
    session: AsyncSession = Depends(get_session),
):

    if date_to < date_from:
        raise HTTPException(status_code=400, detail="Параметр to не может быть раньше from")
    await _require_client(session, client_id)
    try:
        out = await compute_project_team_workload(
            session,
            client_id=client_id,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            include_archived=include_archived,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if out is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return out


@router.post("/{client_id}/projects", response_model=TimeManagerClientProjectOut)
async def create_client_project(
    client_id: str,
    body: TimeManagerClientProjectCreateBody,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(None, alias="Authorization"),
):
    await _require_client_mutable(session, client_id)
    _validate_date_range(body.start_date, body.end_date)
    repo = ClientProjectRepository(session)
    if await repo.has_code_conflict(client_id, body.code):
        raise HTTPException(
            status_code=409,
            detail="Another project with this code already exists for this client",
        )
    budget_amount = body.budget_amount
    if body.project_type == ProjectType.fixed_fee:
        if _positive_budget_amount(body.fixed_fee_amount) and not _positive_budget_amount(budget_amount):
            budget_amount = body.fixed_fee_amount
        if not _positive_budget_amount(budget_amount):
            raise HTTPException(
                status_code=400,
                detail="Для фикс-проекта задайте сумму в поле бюджета (budgetAmount); при пакете «сумма + часы» добавьте лимит часов (budgetHours).",
            )
    money_for_type = budget_amount
    if body.project_type in (ProjectType.time_and_materials, ProjectType.non_billable):
        if not _positive_budget_amount(money_for_type) and body.progress_budget_amount is not None:
            if _d(body.progress_budget_amount) > 0:
                money_for_type = body.progress_budget_amount
    _bt_persist = normalize_budget_type_for_persist(
        body.budget_hours,
        money_for_type if _positive_budget_amount(money_for_type) else None,
    )
    _budget_type_create = _bt_persist if _bt_persist is not None else body.budget_type
    fixed_fee_stored = (
        budget_amount
        if body.project_type == ProjectType.fixed_fee and _positive_budget_amount(budget_amount)
        else body.fixed_fee_amount
    )
    try:
        row = await repo.create(
            client_id=client_id,
            name=body.name,
            code=body.code,
            start_date=body.start_date,
            end_date=body.end_date,
            notes=body.notes,
            report_visibility=body.report_visibility.value,
            project_type=body.project_type.value,
            currency=body.currency.value if body.currency else "USD",
            billable_rate_type=body.billable_rate_type,
            project_billable_rate_amount=body.project_billable_rate_amount,
            budget_type=_budget_type_create,
            budget_amount=budget_amount,
            progress_budget_amount=body.progress_budget_amount,
            budget_hours=body.budget_hours,
            budget_resets_every_month=body.budget_resets_every_month,
            budget_includes_expenses=body.budget_includes_expenses,
            send_budget_alerts=body.send_budget_alerts,
            budget_alert_threshold_percent=body.budget_alert_threshold_percent,
            fixed_fee_amount=fixed_fee_stored,
            is_archived=body.is_archived,
        )
        await session.flush()
        await seed_default_common_tasks_for_project(session, str(row.id))
        members = body.initial_project_access_members or []
        if members:
            initial = list(dict.fromkeys(int(m.auth_user_id) for m in members))
            amount_by_uid = {int(m.auth_user_id): m.billable_hourly_amount for m in members}
        else:
            raw_ids = [int(x) for x in (body.initial_time_tracking_user_auth_ids or [])]
            amts = body.initial_time_tracking_user_billable_hourly_amounts or []
            initial = list(dict.fromkeys(raw_ids))
            amount_by_uid = {}
            if amts:
                for i, uid in enumerate(raw_ids):
                    amount_by_uid[uid] = amts[i]
        if initial:
            par = UserProjectAccessRepository(session)
            proj_currency = row.currency or "USD"
            pid_str = str(row.id)
            has_members_payload = bool(members)
            has_parallel_amounts_payload = bool(
                body.initial_time_tracking_user_billable_hourly_amounts
            )
            for uid in initial:
                if await TimeTrackingUserRepository(session).get_by_auth_user_id(uid) is None:
                    await ensure_time_tracking_user_from_auth(session, authorization, uid)
                applied_project_scoped_rate = False
                amt = None
                if not project_uses_shared_billable(row):
                    amt = amount_by_uid.get(uid)
                    if amt is not None and _d(amt) > 0:
                        await upsert_user_project_scoped_billable_rate(
                            session,
                            auth_user_id=uid,
                            project_id=pid_str,
                            amount=_d(amt),
                            currency=proj_currency,
                            valid_from=row.start_date,
                            valid_to=row.end_date,
                        )
                        applied_project_scoped_rate = True
                # If we just upserted a positive project-scoped rate in this request,
                # skip legacy validation to avoid false negatives in create flow.
                should_validate_rates = project_uses_shared_billable(row) or (
                    not applied_project_scoped_rate
                    and (
                        (has_members_payload and (amt is None or _d(amt) <= 0))
                        or has_parallel_amounts_payload
                    )
                )
                if should_validate_rates:
                    await validate_hourly_rates_for_project_access(
                        session, auth_user_id=uid, project_ids=[str(row.id)]
                    )
                await par.grant_access_if_absent(
                    uid,
                    str(row.id),
                    granted_by_auth_user_id=body.access_granted_by_auth_user_id,
                    projects=repo,
                )
            await session.flush()
            await ensure_projects_have_partner_assignee(
                session,
                par,
                {str(row.id)},
                projects=repo,
                authorization=authorization,
            )
        await sync_project_billable_rates_to_assigned_users(session, str(row.id))
        await session.commit()
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another project with this code already exists for this client",
        ) from None
    await session.refresh(row)
    usage = await repo.time_entries_count(row.id)
    return _project_out(row, usage)


@router.patch(
    "/{client_id}/projects/{project_id}",
    response_model=TimeManagerClientProjectOut,
)
async def patch_client_project(
    client_id: str,
    project_id: str,
    body: TimeManagerClientProjectPatchBody,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientProjectRepository(session)
    patch = body.model_dump(exclude_unset=True, by_alias=False)
    if not patch:
        raise HTTPException(status_code=400, detail="No fields to update")

    row = await repo.get_by_id(client_id, project_id)
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")

    if "code" in patch and patch["code"] is not None:
        if await repo.has_code_conflict(client_id, str(patch["code"]), exclude_project_id=project_id):
            raise HTTPException(
                status_code=409,
                detail="Another project with this code already exists for this client",
            )

    merged_start = row.start_date
    merged_end = row.end_date
    if "start_date" in patch:
        merged_start = patch["start_date"]
    if "end_date" in patch:
        merged_end = patch["end_date"]
    _validate_date_range(merged_start, merged_end)

    if "report_visibility" in patch and patch["report_visibility"] is not None:
        rv = patch["report_visibility"]
        patch["report_visibility"] = rv.value if hasattr(rv, "value") else str(rv)
    if "project_type" in patch and patch["project_type"] is not None:
        pt = patch["project_type"]
        patch["project_type"] = pt.value if hasattr(pt, "value") else str(pt)
    if "currency" in patch and patch["currency"] is not None:
        cur = patch["currency"]
        patch["currency"] = cur.value if hasattr(cur, "value") else str(cur)
    if "is_archived" in patch:
        patch["is_archived"] = bool(patch["is_archived"])

    if any(
        k in patch
        for k in (
            "budget_hours",
            "budget_amount",
            "progress_budget_amount",
            "budget_type",
            "project_type",
            "fixed_fee_amount",
        )
    ):
        m_h = patch["budget_hours"] if "budget_hours" in patch else row.budget_hours
        m_a = patch["budget_amount"] if "budget_amount" in patch else row.budget_amount
        m_pb = (
            patch["progress_budget_amount"]
            if "progress_budget_amount" in patch
            else row.progress_budget_amount
        )
        eff_pt = patch["project_type"] if "project_type" in patch else row.project_type
        if eff_pt == "fixed_fee":
            if "fixed_fee_amount" in patch and patch["fixed_fee_amount"] is not None:
                ffa = patch["fixed_fee_amount"]
                if not _positive_budget_amount(m_a) and _positive_budget_amount(ffa):
                    m_a = ffa
                    patch["budget_amount"] = m_a
            if not _positive_budget_amount(m_a) and _positive_budget_amount(
                getattr(row, "fixed_fee_amount", None)
            ):
                m_a = row.fixed_fee_amount
                patch["budget_amount"] = m_a
            if not _positive_budget_amount(m_a):
                raise HTTPException(
                    status_code=400,
                    detail="Для фикс-проекта укажите сумму в budgetAmount (бюджет).",
                )
            patch["fixed_fee_amount"] = m_a
            nt = normalize_budget_type_for_persist(
                m_h,
                m_a if _positive_budget_amount(m_a) else None,
            )
        else:
            money_for_type = m_a
            if eff_pt in ("time_and_materials", "non_billable") and not _positive_budget_amount(
                money_for_type
            ):
                if m_pb is not None and _d(m_pb) > 0:
                    money_for_type = m_pb
            nt = normalize_budget_type_for_persist(
                m_h,
                money_for_type if _positive_budget_amount(money_for_type) else None,
            )
        patch["budget_type"] = nt

    try:
        updated = await repo.update(client_id, project_id, patch)
        if not updated:
            raise HTTPException(status_code=404, detail="Project not found")
        await reapply_project_billable_mode(
            session,
            project_id,
            updated,
            project_row_before=row,
            patch=patch,
        )
        await session.commit()
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=409,
            detail="Another project with this code already exists for this client",
        ) from None
    except Exception as e:
        await session.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось сохранить проект: {e}",
        ) from e

    try:
        await session.refresh(updated)
        usage = await repo.time_entries_count(updated.id)
        return _project_out(updated, usage)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Проект сохранён, но ответ не сформирован: {e}",
        ) from e


@router.delete("/{client_id}/projects/{project_id}", status_code=204)
async def delete_client_project(
    client_id: str,
    project_id: str,
    session: AsyncSession = Depends(get_session),
):
    await _require_client_mutable(session, client_id)
    repo = ClientProjectRepository(session)
    usage = await repo.time_entries_count(project_id)
    if usage > 0:
        raise HTTPException(
            status_code=409,
            detail="Project has time entries and cannot be deleted",
        )
    ok = await repo.delete(client_id, project_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Project not found")
    await session.commit()
    return Response(status_code=204)
