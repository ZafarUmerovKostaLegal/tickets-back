

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import ensure_time_entry_subject_allowed
from application.project_billable_rate_sync import sync_project_billable_rates_to_assigned_users
from application.project_partner_requirement import ensure_projects_have_partner_assignee
from application.project_access_rates import validate_hourly_rates_for_project_access
from infrastructure.database import get_session
from infrastructure.repositories import (
    ClientProjectRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)
from presentation.deps import require_bearer_user
from presentation.schemas import ProjectAccessOut, ProjectAccessPutBody

router = APIRouter(prefix="/users", tags=["project_access"])


async def _ensure_user(session: AsyncSession, auth_user_id: int) -> None:
    ur = TimeTrackingUserRepository(session)
    if not await ur.get_by_auth_user_id(auth_user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")


@router.get("/{auth_user_id}/project-access", response_model=ProjectAccessOut)
async def get_project_access(
    auth_user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> ProjectAccessOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=False)
    await _ensure_user(session, auth_user_id)
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
    await _ensure_user(session, auth_user_id)
    repo = UserProjectAccessRepository(session)
    projects = ClientProjectRepository(session)
    try:
        await validate_hourly_rates_for_project_access(
            session, auth_user_id=auth_user_id, project_ids=list(body.project_ids)
        )
        affected = await repo.replace_all(
            auth_user_id,
            list(body.project_ids),
            granted_by_auth_user_id=body.granted_by_auth_user_id,
            projects=projects,
        )
        await ensure_projects_have_partner_assignee(
            session, repo, affected, projects=projects, authorization=authorization
        )
        for pid in affected:
            await sync_project_billable_rates_to_assigned_users(session, pid)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    ids = await repo.list_project_ids(auth_user_id)
    return ProjectAccessOut(project_ids=ids)
