

from __future__ import annotations

import logging
import traceback
from datetime import date
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

_log = logging.getLogger(__name__)

from application.services.reports.time_report_service import (
    get_time_report,
    get_time_report_flat_entries,
    get_time_report_summary_for_export,
)
from application.services.reports.expense_report_service import (
    get_expense_report,
    get_expense_report_all_rows,
)
from application.services.reports.uninvoiced_report_service import (
    get_uninvoiced_report,
    get_uninvoiced_report_all_rows,
)
from application.services.reports.budget_report_service import (
    get_budget_report,
    get_budget_report_all_rows,
)
from application.services.reports.export_service import export_report
from infrastructure.database import get_session
from infrastructure.repository_users import TimeTrackingUserRepository
from presentation.schemas_reports import (
    ExportFormat,
    ExpenseGroupBy,
    ReportMetaOut,
    ReportResponseOut,
    ReportUserForFilterOut,
    TimeGroupBy,
)

router = APIRouter(prefix="/reports", tags=["reports"])

_TIME_GROUP_OPTIONS = frozenset({"clients", "projects"})
_EXPENSE_GROUP_OPTIONS = frozenset({"clients", "projects", "categories", "team"})


def _parse_date(v: str | None, name: str) -> date:
    if not v:
        raise HTTPException(status_code=400, detail=f"{name} is required")
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date for {name}: {v}")


def _report_period(
    frm: str | None = Query(
        None,
        alias="from",
        description="Начало периода YYYY-MM-DD (можно вместо этого передать dateFrom)",
    ),
    to_q: str | None = Query(
        None,
        alias="to",
        description="Конец периода YYYY-MM-DD включительно (можно вместо этого передать dateTo)",
    ),
    date_from: str | None = Query(
        None,
        alias="dateFrom",
        description="Начало периода YYYY-MM-DD (альтернатива параметру from)",
    ),
    date_to: str | None = Query(
        None,
        alias="dateTo",
        description="Конец периода YYYY-MM-DD включительно (альтернатива параметру to)",
    ),
) -> tuple[date, date]:

    start_raw = (date_from or frm or "").strip()
    end_raw = (date_to or to_q or "").strip()
    if not start_raw or not end_raw:
        raise HTTPException(
            status_code=400,
            detail="Укажите период: from и to (YYYY-MM-DD) или dateFrom и dateTo.",
        )
    d0 = _parse_date(start_raw, "from")
    d1 = _parse_date(end_raw, "to")
    if d1 < d0:
        raise HTTPException(
            status_code=400,
            detail="Конец периода (to / dateTo) не может быть раньше начала (from / dateFrom).",
        )
    return d0, d1


ReportPeriod = Annotated[tuple[date, date], Depends(_report_period)]


def _parse_ids_int(raw: str | None) -> list[int] | None:
    if not raw:
        return None
    out: list[int] = []
    for part in raw.split(","):
        s = part.strip()
        if s:
            try:
                out.append(int(s))
            except ValueError:
                pass
    return out or None


def _parse_ids_str(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    out = [s.strip() for s in raw.split(",") if s.strip()]
    return out or None


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None:
        return None
    return raw.lower() in ("1", "true", "yes")


@router.get("/meta", response_model=ReportMetaOut)
async def get_reports_meta():
    return ReportMetaOut(
        reportTypes=sorted(["time", "expenses", "uninvoiced", "project-budget"]),
        groupOptions=sorted(list(_TIME_GROUP_OPTIONS | _EXPENSE_GROUP_OPTIONS)),
    )


@router.get("/users-for-filter", response_model=list[ReportUserForFilterOut])
async def get_users_for_filter(session: AsyncSession = Depends(get_session)):
    repo = TimeTrackingUserRepository(session)
    users = await repo.list_users()
    return [
        ReportUserForFilterOut(
            id=u.auth_user_id,
            displayName=u.display_name,
            email=u.email,
        )
        for u in users
        if not u.is_archived
    ]


@router.get(
    "/time/{group_by}",
    response_model=ReportResponseOut,
    summary="Time report grouped by clients or projects",
)
async def get_time_report_endpoint(
    group_by: TimeGroupBy,
    period: ReportPeriod,
    client_id: Optional[str] = Query(None, description="Comma-separated client IDs"),
    project_id: Optional[str] = Query(None, description="Comma-separated project IDs"),
    user_id: Optional[str] = Query(None, description="Comma-separated user IDs (int)"),
    task_id: Optional[str] = Query(None, description="Comma-separated task IDs"),
    is_billable: Optional[str] = Query(None, description="true/false"),
    include_fixed_fee: bool = Query(True, alias="include_fixed_fee"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        return await get_time_report(
            session,
            group_by=group_by.value,
            date_from=df,
            date_to=dt,
            client_ids=_parse_ids_str(client_id),
            project_ids=_parse_ids_str(project_id),
            user_ids=_parse_ids_int(user_id),
            task_ids=_parse_ids_str(task_id),
            is_billable=_parse_bool(is_billable),
            include_fixed_fee=include_fixed_fee,
            page=page,
            per_page=per_page,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/time/%s error: %s\n%s", group_by, exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Time report error: {exc}")


@router.get(
    "/time/{group_by}/export",
    summary="Export time report as CSV or XLSX",
)
async def export_time_report_endpoint(
    group_by: TimeGroupBy,
    period: ReportPeriod,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    task_id: Optional[str] = Query(None),
    is_billable: Optional[str] = Query(None),
    include_fixed_fee: bool = Query(True, alias="include_fixed_fee"),
    format: ExportFormat = Query(ExportFormat.csv),
    export: str = Query(
        "detail",
        description="detail: построчно (как раньше); summary: одна строка на группу, как в превью отчёта",
    ),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        ex = (export or "detail").strip().lower()
        if ex not in ("detail", "summary"):
            raise HTTPException(
                status_code=400,
                detail="Параметр export: detail (по умолчанию) или summary",
            )
        if ex == "summary":
            rows = await get_time_report_summary_for_export(
                session,
                group_by=group_by.value,
                date_from=df,
                date_to=dt,
                client_ids=_parse_ids_str(client_id),
                project_ids=_parse_ids_str(project_id),
                user_ids=_parse_ids_int(user_id),
                task_ids=_parse_ids_str(task_id),
                is_billable=_parse_bool(is_billable),
                include_fixed_fee=include_fixed_fee,
            )
        else:
            rows = await get_time_report_flat_entries(
                session,
                group_by=group_by.value,
                date_from=df,
                date_to=dt,
                client_ids=_parse_ids_str(client_id),
                project_ids=_parse_ids_str(project_id),
                user_ids=_parse_ids_int(user_id),
                task_ids=_parse_ids_str(task_id),
                is_billable=_parse_bool(is_billable),
                include_fixed_fee=include_fixed_fee,
            )
        return export_report(rows, format.value, "time", group_by.value, df, dt)
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/time/%s/export error: %s\n%s", group_by, exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Time report export error: {exc}")


@router.get(
    "/expenses/{group_by}",
    response_model=ReportResponseOut,
    summary="Expense report grouped by clients/projects/categories/team",
)
async def get_expense_report_endpoint(
    group_by: ExpenseGroupBy,
    period: ReportPeriod,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        return await get_expense_report(
            session,
            group_by=group_by.value,
            date_from=df,
            date_to=dt,
            client_ids=_parse_ids_str(client_id),
            project_ids=_parse_ids_str(project_id),
            user_ids=_parse_ids_int(user_id),
            page=page,
            per_page=per_page,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/expenses/%s error: %s\n%s", group_by, exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Expense report error: {exc}")


@router.get(
    "/expenses/{group_by}/export",
    summary="Export expense report as CSV or XLSX",
)
async def export_expense_report_endpoint(
    group_by: ExpenseGroupBy,
    period: ReportPeriod,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    format: ExportFormat = Query(ExportFormat.csv),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        rows = await get_expense_report_all_rows(
            session,
            group_by=group_by.value,
            date_from=df,
            date_to=dt,
            client_ids=_parse_ids_str(client_id),
            project_ids=_parse_ids_str(project_id),
            user_ids=_parse_ids_int(user_id),
        )
        return export_report(rows, format.value, "expenses", group_by.value, df, dt)
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/expenses/%s/export error: %s\n%s", group_by, exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Expense report export error: {exc}")


@router.get(
    "/uninvoiced",
    response_model=ReportResponseOut,
    summary="Uninvoiced hours and expenses report",
)
async def get_uninvoiced_report_endpoint(
    period: ReportPeriod,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    include_fixed_fee: bool = Query(True, alias="include_fixed_fee"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        return await get_uninvoiced_report(
            session,
            date_from=df,
            date_to=dt,
            client_ids=_parse_ids_str(client_id),
            project_ids=_parse_ids_str(project_id),
            user_ids=_parse_ids_int(user_id),
            include_fixed_fee=include_fixed_fee,
            page=page,
            per_page=per_page,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/uninvoiced error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Uninvoiced report error: {exc}")


@router.get(
    "/uninvoiced/export",
    summary="Export uninvoiced report as CSV or XLSX",
)
async def export_uninvoiced_report_endpoint(
    period: ReportPeriod,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    include_fixed_fee: bool = Query(True, alias="include_fixed_fee"),
    format: ExportFormat = Query(ExportFormat.csv),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        rows = await get_uninvoiced_report_all_rows(
            session,
            date_from=df,
            date_to=dt,
            client_ids=_parse_ids_str(client_id),
            project_ids=_parse_ids_str(project_id),
            user_ids=_parse_ids_int(user_id),
            include_fixed_fee=include_fixed_fee,
        )
        return export_report(rows, format.value, "uninvoiced", None, df, dt)
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/uninvoiced/export error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Uninvoiced report export error: {exc}")


@router.get(
    "/project-budget",
    response_model=ReportResponseOut,
    summary="Project budget report with budget_spent and budget_remaining",
)
async def get_budget_report_endpoint(
    period: ReportPeriod,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    include_fixed_fee: bool = Query(True, alias="include_fixed_fee"),
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        return await get_budget_report(
            session,
            date_from=df,
            date_to=dt,
            client_ids=_parse_ids_str(client_id),
            project_ids=_parse_ids_str(project_id),
            user_ids=_parse_ids_int(user_id),
            include_fixed_fee=include_fixed_fee,
            page=page,
            per_page=per_page,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/project-budget error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Project budget report error: {exc}")


@router.get(
    "/project-budget/export",
    summary="Export project budget report as CSV or XLSX",
)
async def export_budget_report_endpoint(
    period: ReportPeriod,
    client_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    include_fixed_fee: bool = Query(True, alias="include_fixed_fee"),
    format: ExportFormat = Query(ExportFormat.csv),
    session: AsyncSession = Depends(get_session),
):
    df, dt = period
    try:
        rows = await get_budget_report_all_rows(
            session,
            date_from=df,
            date_to=dt,
            client_ids=_parse_ids_str(client_id),
            project_ids=_parse_ids_str(project_id),
            user_ids=_parse_ids_int(user_id),
            include_fixed_fee=include_fixed_fee,
        )
        return export_report(rows, format.value, "project-budget", None, df, dt)
    except HTTPException:
        raise
    except Exception as exc:
        _log.error("reports/project-budget/export error: %s\n%s", exc, traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Project budget export error: {exc}")
