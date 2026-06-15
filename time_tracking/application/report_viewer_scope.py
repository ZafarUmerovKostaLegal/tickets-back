
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import _MANAGE_ROLES_TIME_ENTRIES, _org_role
from application.project_partner_users import list_partner_auth_user_ids_for_project
from infrastructure.models_reports import ReportPartnerConfirmationRequestModel
from infrastructure.repository_access import UserProjectAccessRepository
from infrastructure.repository_partner_report_confirmations import _STATUS_CONFIRMED


_REPORTS_UNSCOPED_ROLES = frozenset(
    {
        "Главный администратор",
        "Администратор",
        "IT отдел",
        "Офис менеджер",
    }
)


def _viewer_id(viewer: dict) -> int:
    uid = viewer.get("id")
    if uid is None:
        raise HTTPException(status_code=403, detail="В токене нет id пользователя")
    return int(uid)


def viewer_sees_all_report_projects(viewer: dict) -> bool:
    role = _org_role(viewer)
    if role in _MANAGE_ROLES_TIME_ENTRIES:
        return True
    return role in _REPORTS_UNSCOPED_ROLES


async def list_partner_project_ids_for_viewer(
    session: AsyncSession,
    viewer: dict,
    *,
    authorization: str | None,
) -> list[str]:
    vid = _viewer_id(viewer)
    access_repo = UserProjectAccessRepository(session)
    out: list[str] = []
    for pid in await access_repo.list_project_ids(vid):
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, pid, authorization=authorization
        )
        if vid in partners:
            out.append(pid)
    return sorted(set(out))


def merge_requested_project_ids(
    scope: list[str] | None,
    requested: list[str] | None,
) -> list[str] | None:
    if scope is None:
        return requested
    if not scope:
        return []
    if not requested:
        return scope
    allowed = [p for p in requested if p in scope]
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail="Нет доступа к указанным проектам в отчётах",
        )
    return allowed


async def resolve_report_project_ids(
    session: AsyncSession,
    viewer: dict,
    *,
    authorization: str | None,
    requested_project_ids: list[str] | None,
) -> list[str] | None:
    if viewer_sees_all_report_projects(viewer):
        return requested_project_ids
    partner_pids = await list_partner_project_ids_for_viewer(
        session, viewer, authorization=authorization
    )
    return merge_requested_project_ids(partner_pids, requested_project_ids)


async def list_fully_confirmed_project_ids_overlapping_period(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    *,
    limit_to_project_ids: list[str] | None = None,
) -> list[str]:
    if date_to < date_from:
        return []
    q = (
        select(ReportPartnerConfirmationRequestModel.project_id)
        .where(
            and_(
                ReportPartnerConfirmationRequestModel.status == _STATUS_CONFIRMED,
                ReportPartnerConfirmationRequestModel.date_from <= date_to,
                ReportPartnerConfirmationRequestModel.date_to >= date_from,
            )
        )
        .distinct()
    )
    if limit_to_project_ids is not None:
        if not limit_to_project_ids:
            return []
        q = q.where(
            ReportPartnerConfirmationRequestModel.project_id.in_(limit_to_project_ids)
        )
    rows = (await session.execute(q)).scalars().all()
    return sorted({str(r).strip() for r in rows if r and str(r).strip()})


async def apply_partner_confirmed_only_filter(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    project_ids: list[str] | None,
    *,
    enabled: bool,
) -> list[str] | None:
    if not enabled:
        return project_ids
    return await list_fully_confirmed_project_ids_overlapping_period(
        session,
        date_from,
        date_to,
        limit_to_project_ids=project_ids,
    )


def viewer_can_see_all_partner_confirmations(viewer: dict) -> bool:
    return viewer_sees_all_report_projects(viewer)
