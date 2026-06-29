

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import ensure_time_entry_subject_allowed
from application.auth_user_directory import ensure_time_tracking_user_from_auth
from application.manual_tt_users import is_manual_tt_auth_user_id
from application.project_access_notifications import run_project_access_added_notifications_safe
from application.project_billable_rate_sync import (
    project_uses_shared_billable,
    sync_project_billable_rates_to_assigned_users,
    upsert_user_project_scoped_billable_rate,
)
from application.project_partner_requirement import ensure_projects_have_partner_assignee
from application.project_access_rates import validate_hourly_rates_for_project_access
from infrastructure.database import get_session
from infrastructure.repositories import ClientProjectRepository, TimeTrackingUserRepository, UserProjectAccessRepository
from presentation.deps import require_bearer_user
from presentation.schemas import ProjectAccessOut, ProjectAccessPutBody

router = APIRouter(prefix="/users", tags=["project_access"])


async def _ensure_user(
    session: AsyncSession,
    authorization: str | None,
    auth_user_id: int,
) -> None:
    tur = TimeTrackingUserRepository(session)
    if await tur.get_by_auth_user_id(auth_user_id) is not None:
        return
    if is_manual_tt_auth_user_id(auth_user_id):
        raise HTTPException(
            status_code=404,
            detail="Пользователь учёта времени без auth не найден — создайте его через POST /users/manual",
        )
    try:
        await ensure_time_tracking_user_from_auth(session, authorization, auth_user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{auth_user_id}/project-access", response_model=ProjectAccessOut)
async def get_project_access(
    auth_user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> ProjectAccessOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=False)
    await _ensure_user(session, authorization, auth_user_id)
    repo = UserProjectAccessRepository(session)
    ids = await repo.list_project_ids(auth_user_id)
    return ProjectAccessOut(project_ids=ids)


@router.put("/{auth_user_id}/project-access", response_model=ProjectAccessOut)
async def put_project_access(
    auth_user_id: int,
    body: ProjectAccessPutBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> ProjectAccessOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, authorization, auth_user_id)
    repo = UserProjectAccessRepository(session)
    projects = ClientProjectRepository(session)
    try:
        old_pids = await repo.list_project_ids(auth_user_id)
        old_set = set(old_pids)
        seen: set[str] = set()
        normalized: list[str] = []
        for raw in body.project_ids:
            pid = (raw or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            normalized.append(pid)
        new_set = set(normalized)
        newly_added = [p for p in normalized if p not in old_set]
        delta_pids = old_set.symmetric_difference(new_set)

        raw_rates = body.project_billable_hourly_amounts_by_project_id or {}
        rates_norm = {(k or "").strip(): v for k, v in raw_rates.items() if (k or "").strip()}
        for pid_key in normalized:
            proj_row = await projects.get_by_id_global(pid_key)
            if not proj_row or project_uses_shared_billable(proj_row):
                continue
            amt = rates_norm.get(pid_key)
            if amt is not None and amt > 0:
                await upsert_user_project_scoped_billable_rate(
                    session,
                    auth_user_id=auth_user_id,
                    project_id=pid_key,
                    amount=amt,
                    currency=proj_row.currency or "USD",
                    valid_from=None,
                    valid_to=None,
                )
        await validate_hourly_rates_for_project_access(
            session, auth_user_id=auth_user_id, project_ids=newly_added
        )
        await repo.replace_all(
            auth_user_id,
            normalized,
            granted_by_auth_user_id=body.granted_by_auth_user_id,
            projects=projects,
        )
        await ensure_projects_have_partner_assignee(
            session, repo, delta_pids, projects=projects, authorization=authorization
        )
        for pid in delta_pids:
            await sync_project_billable_rates_to_assigned_users(session, pid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    if newly_added:
        await run_project_access_added_notifications_safe(
            session,
            auth_user_id=auth_user_id,
            project_ids=newly_added,
        )
    ids = await repo.list_project_ids(auth_user_id)
    return ProjectAccessOut(project_ids=ids)
