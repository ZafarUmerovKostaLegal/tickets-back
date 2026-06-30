from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application.labor_statistics_service import (
    LaborStatisticsQuery,
    build_labor_statistics,
    build_labor_statistics_meta,
    export_labor_statistics,
)
from infrastructure.database import get_session
from presentation.deps import require_tt_reports_viewer

router = APIRouter(prefix="/statistics/labor", tags=["statistics_labor"])


def _parse_date(raw: str, label: str) -> date:
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Некорректная дата {label}") from exc


def _query_from_params(
    *,
    date_from: str,
    date_to: str,
    partner_id: str | None,
    team_id: str | None,
    client_id: str | None,
    project_id: str | None,
    work_type_id: str | None,
    lawyer_id: str | None,
    project_status_id: str | None,
    active_projects_only: bool,
    q: str | None,
    sort: str,
    sort_dir: str,
    page: int,
    per_page: int,
) -> LaborStatisticsQuery:
    return LaborStatisticsQuery(
        date_from=_parse_date(date_from, "date_from"),
        date_to=_parse_date(date_to, "date_to"),
        partner_id=partner_id,
        team_id=team_id,
        client_id=client_id,
        project_id=project_id,
        work_type_id=work_type_id,
        lawyer_id=lawyer_id,
        project_status_id=project_status_id,
        active_projects_only=active_projects_only,
        q=q,
        sort=sort,
        sort_dir=sort_dir,
        page=page,
        per_page=per_page,
    )


@router.get("/meta")
async def labor_statistics_meta(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_tt_reports_viewer),
    authorization: str | None = Header(None, alias="Authorization"),
):
    return await build_labor_statistics_meta(session, viewer, authorization=authorization)


@router.get("")
async def labor_statistics_dashboard(
    date_from: str = Query(..., alias="date_from"),
    date_to: str = Query(..., alias="date_to"),
    partner_id: str | None = Query(None, alias="partner_id"),
    team_id: str | None = Query(None, alias="team_id"),
    client_id: str | None = Query(None, alias="client_id"),
    project_id: str | None = Query(None, alias="project_id"),
    work_type_id: str | None = Query(None, alias="work_type_id"),
    lawyer_id: str | None = Query(None, alias="lawyer_id"),
    project_status_id: str | None = Query(None, alias="project_status_id"),
    active_projects_only: bool = Query(False, alias="active_projects_only"),
    q: str | None = Query(None),
    sort: str = Query("hours"),
    sort_dir: str = Query("desc", alias="sort_dir"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200, alias="per_page"),
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_tt_reports_viewer),
    authorization: str | None = Header(None, alias="Authorization"),
):
    query = _query_from_params(
        date_from=date_from,
        date_to=date_to,
        partner_id=partner_id,
        team_id=team_id,
        client_id=client_id,
        project_id=project_id,
        work_type_id=work_type_id,
        lawyer_id=lawyer_id,
        project_status_id=project_status_id,
        active_projects_only=active_projects_only,
        q=q,
        sort=sort,
        sort_dir=sort_dir,
        page=page,
        per_page=per_page,
    )
    return await build_labor_statistics(session, viewer, query, authorization=authorization)


@router.get("/export")
async def labor_statistics_export(
    date_from: str = Query(..., alias="date_from"),
    date_to: str = Query(..., alias="date_to"),
    export_format: Literal["csv", "xlsx"] = Query("csv", alias="format"),
    partner_id: str | None = Query(None, alias="partner_id"),
    team_id: str | None = Query(None, alias="team_id"),
    client_id: str | None = Query(None, alias="client_id"),
    project_id: str | None = Query(None, alias="project_id"),
    work_type_id: str | None = Query(None, alias="work_type_id"),
    lawyer_id: str | None = Query(None, alias="lawyer_id"),
    project_status_id: str | None = Query(None, alias="project_status_id"),
    active_projects_only: bool = Query(False, alias="active_projects_only"),
    q: str | None = Query(None),
    sort: str = Query("hours"),
    sort_dir: str = Query("desc", alias="sort_dir"),
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_tt_reports_viewer),
    authorization: str | None = Header(None, alias="Authorization"),
):
    query = _query_from_params(
        date_from=date_from,
        date_to=date_to,
        partner_id=partner_id,
        team_id=team_id,
        client_id=client_id,
        project_id=project_id,
        work_type_id=work_type_id,
        lawyer_id=lawyer_id,
        project_status_id=project_status_id,
        active_projects_only=active_projects_only,
        q=q,
        sort=sort,
        sort_dir=sort_dir,
        page=1,
        per_page=50,
    )
    return await export_labor_statistics(
        session,
        viewer,
        query,
        export_format=export_format,
        authorization=authorization,
    )
