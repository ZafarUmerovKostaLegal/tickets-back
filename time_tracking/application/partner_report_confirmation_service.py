
from __future__ import annotations

from datetime import date

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import _MANAGE_ROLES_TIME_ENTRIES, _VIEW_ROLES_TIME_ENTRIES
from application.project_partner_users import list_partner_auth_user_ids_for_project
from infrastructure.repository_access import UserProjectAccessRepository
from infrastructure.repository_clients import ClientProjectRepository
from infrastructure.repository_partner_report_confirmations import (
    PartnerReportConfirmationRepository,
    project_id_from_snapshot_row,
)
from infrastructure.repository_reports import ReportSnapshotRepository


def _org_role(viewer: dict) -> str:
    return (viewer.get("role") or "").strip()


def _viewer_id(viewer: dict) -> int:
    uid = viewer.get("id")
    if uid is None:
        raise HTTPException(status_code=403, detail="В токене нет id пользователя")
    return int(uid)


def _viewer_can_see_all_confirmations(viewer: dict) -> bool:
    return _org_role(viewer) in (_MANAGE_ROLES_TIME_ENTRIES | _VIEW_ROLES_TIME_ENTRIES)


async def build_report_confirmation_title(
    session: AsyncSession,
    projects: ClientProjectRepository,
    project_id: str,
    date_from: date,
    date_to: date,
) -> str:
    pr = await projects.get_by_id_global(project_id)
    code = (project_id or "").strip()
    if pr is not None:
        if pr.code and str(pr.code).strip():
            code = str(pr.code).strip()
        elif pr.name and str(pr.name).strip():
            code = str(pr.name).strip()
    return f"{code} {date_from.isoformat()}–{date_to.isoformat()}"


def _request_to_out(m, required_partners: list[int]) -> dict:
    sigs = [
        {
            "partnerAuthUserId": s.partner_auth_user_id,
            "confirmedAt": s.confirmed_at.isoformat(),
        }
        for s in (m.signatures or [])
    ]
    signed_ids = {s.partner_auth_user_id for s in (m.signatures or [])}
    pending_ids = [p for p in required_partners if p not in signed_ids]
    return {
        "id": m.id,
        "snapshotId": m.snapshot_id,
        "projectId": m.project_id,
        "dateFrom": m.date_from.isoformat(),
        "dateTo": m.date_to.isoformat(),
        "title": m.title,
        "status": m.status,
        "submittedByAuthUserId": m.submitted_by_auth_user_id,
        "requiredPartnerAuthUserIds": list(required_partners),
        "pendingPartnerAuthUserIds": pending_ids,
        "signatures": sigs,
        "createdAt": m.created_at.isoformat(),
        "updatedAt": m.updated_at.isoformat() if m.updated_at else None,
    }


async def submit_partner_report_confirmation(
    session: AsyncSession,
    viewer: dict,
    *,
    snapshot_id: str,
    project_id: str,
    date_from: date,
    date_to: date,
    authorization: str | None,
) -> dict:
    vid = _viewer_id(viewer)
    snap_repo = ReportSnapshotRepository(session)
    snap = await snap_repo.get_by_id(snapshot_id, load_rows=False)
    if not snap or snap.created_by_user_id != vid:
        raise HTTPException(status_code=404, detail="Снимок отчёта не найден")
    conf_repo = PartnerReportConfirmationRepository(session)
    if not await conf_repo.snapshot_has_project_row(snapshot_id, project_id):
        raise HTTPException(
            status_code=400,
            detail="В снимке нет строк по указанному проекту",
        )
    access_repo = UserProjectAccessRepository(session)
    partners = await list_partner_auth_user_ids_for_project(
        session, access_repo, project_id, authorization=authorization
    )
    if not partners:
        raise HTTPException(
            status_code=400,
            detail="По проекту не найдены партнёры для подтверждения (доступ и роль/должность)",
        )
    projects = ClientProjectRepository(session)
    title = await build_report_confirmation_title(session, projects, project_id, date_from, date_to)
    row = await conf_repo.upsert_submit(
        snapshot_id=snapshot_id,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        title=title,
        submitted_by_auth_user_id=vid,
    )
    await session.commit()
    loaded = await conf_repo.get_request_by_id(row.id, load_signatures=True)
    if not loaded:
        raise HTTPException(status_code=500, detail="internal")
    return _request_to_out(loaded, partners)


async def submit_partner_report_confirmation_from_preview(
    session: AsyncSession,
    viewer: dict,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
    authorization: str | None,
) -> dict:
    """Создаёт минимальный снимок отчёта по проекту и сразу вызывает submit (для предпросмотра без отдельного POST /snapshots)."""
    pid = (project_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="projectId required")
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="dateTo не может быть раньше dateFrom")
    conf_repo = PartnerReportConfirmationRepository(session)
    existing = await conf_repo.find_latest_pending_for_project_period(pid, date_from, date_to)
    if existing:
        access_repo = UserProjectAccessRepository(session)
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, pid, authorization=authorization
        )
        if not partners:
            raise HTTPException(
                status_code=400,
                detail="По проекту не найдены партнёры для подтверждения (доступ и роль/должность)",
            )
        return _request_to_out(existing, partners)
    vid = _viewer_id(viewer)
    projects = ClientProjectRepository(session)
    snap_name = await build_report_confirmation_title(session, projects, pid, date_from, date_to)
    snap_repo = ReportSnapshotRepository(session)
    snap = await snap_repo.create(
        name=snap_name,
        report_type="time",
        group_by="projects",
        filters={
            "dateFrom": date_from.isoformat(),
            "dateTo": date_to.isoformat(),
            "projectIds": [pid],
        },
        created_by_user_id=vid,
        rows_data=[
            {
                "source_type": "project",
                "source_id": pid,
                "data": {"projectId": pid},
            }
        ],
    )
    await session.flush()
    return await submit_partner_report_confirmation(
        session,
        viewer,
        snapshot_id=snap.id,
        project_id=pid,
        date_from=date_from,
        date_to=date_to,
        authorization=authorization,
    )


async def confirm_partner_report_confirmation(
    session: AsyncSession,
    viewer: dict,
    request_id: str,
    *,
    authorization: str | None,
) -> dict:
    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    req = await conf_repo.get_request_by_id(request_id, load_signatures=True)
    if not req:
        raise HTTPException(status_code=404, detail="Запрос на подтверждение не найден")
    access_repo = UserProjectAccessRepository(session)
    partners = await list_partner_auth_user_ids_for_project(
        session, access_repo, req.project_id, authorization=authorization
    )
    if not partners:
        raise HTTPException(status_code=400, detail="По проекту не найдены партнёры")
    if vid not in partners:
        raise HTTPException(status_code=403, detail="Подтверждать могут только партнёры проекта")
    if not await conf_repo.partner_has_signed(request_id, vid):
        await conf_repo.add_signature(request_id, vid)
        await session.flush()
    signed_ids = set(await conf_repo.list_signature_partner_ids(request_id))
    if partners and set(partners).issubset(signed_ids):
        await conf_repo.mark_fully_confirmed(request_id)
    await session.commit()
    req = await conf_repo.get_request_by_id(request_id, load_signatures=True)
    if not req:
        raise HTTPException(status_code=500, detail="internal")
    partners = await list_partner_auth_user_ids_for_project(
        session, access_repo, req.project_id, authorization=authorization
    )
    return _request_to_out(req, partners)


async def list_pending_partner_confirmations(
    session: AsyncSession,
    viewer: dict,
    *,
    authorization: str | None,
) -> list[dict]:
    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    access_repo = UserProjectAccessRepository(session)
    candidates = await conf_repo.list_pending_for_partner(vid)
    out: list[dict] = []
    for m in candidates:
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, m.project_id, authorization=authorization
        )
        if vid not in partners:
            continue
        out.append(_request_to_out(m, partners))
    return out


async def list_confirmed_partner_confirmations(
    session: AsyncSession,
    viewer: dict,
    *,
    authorization: str | None,
) -> list[dict]:
    vid = _viewer_id(viewer)
    conf_repo = PartnerReportConfirmationRepository(session)
    access_repo = UserProjectAccessRepository(session)
    if _viewer_can_see_all_confirmations(viewer):
        rows = await conf_repo.list_all_fully_confirmed()
    else:
        partner_projects: set[str] = set()
        for pid in await access_repo.list_project_ids(vid):
            pals = await list_partner_auth_user_ids_for_project(
                session, access_repo, pid, authorization=authorization
            )
            if vid in pals:
                partner_projects.add(pid)
        rows = await conf_repo.list_confirmed_visible_for(
            vid, partner_project_ids=partner_projects
        )
    out: list[dict] = []
    for m in rows:
        partners = await list_partner_auth_user_ids_for_project(
            session, access_repo, m.project_id, authorization=authorization
        )
        out.append(_request_to_out(m, partners))
    return out


async def invalidate_confirmations_after_row_edit(
    session: AsyncSession,
    snapshot_id: str,
    row_id: str,
) -> None:
    snap_repo = ReportSnapshotRepository(session)
    row = await snap_repo.get_row(snapshot_id, row_id)
    if not row:
        return
    project_id = project_id_from_snapshot_row(row)
    if not project_id:
        return
    conf_repo = PartnerReportConfirmationRepository(session)
    await conf_repo.invalidate_all_for_snapshot_project(snapshot_id, project_id)
    await session.flush()
