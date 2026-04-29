

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from application.auth_user_directory import (
    fetch_auth_user_partner_hints_by_id,
    fetch_auth_user_position,
    fetch_auth_user_positions_by_id,
)
from application.access_control import (
    ensure_can_list_all_tt_users,
    ensure_can_read_tt_user_row,
    ensure_delete_tt_user_allowed,
    ensure_managed_scope_allowed,
    ensure_upsert_user_allowed,
    ensure_weekly_capacity_patch_allowed,
)
from application.project_partner_requirement import (
    ensure_projects_have_partner_assignee,
    user_satisfies_partner_rule,
)
from infrastructure.database import get_session
from infrastructure.repositories import (
    ClientProjectRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)
from presentation.deps import require_bearer_user
from presentation.schemas import UserResponse, UserUpsertBody, WeeklyCapacityPatchBody

router = APIRouter(prefix="/users", tags=["users"])


def _position_merged(row_pos: str | None, auth_map: dict[int, str | None], auth_user_id: int) -> str | None:

    if row_pos is not None and str(row_pos).strip():
        return str(row_pos).strip()
    v = auth_map.get(auth_user_id)
    if v is not None and str(v).strip():
        return str(v).strip()
    return None


def _user_response_directory(
    row,
    *,
    position: str | None,
) -> UserResponse:

    return UserResponse(
        id=row.auth_user_id,
        email=row.email,
        display_name=row.display_name,
        picture=row.picture,
        position=position,
        role="",
        is_blocked=row.is_blocked,
        is_archived=row.is_archived,
        weekly_capacity_hours=row.weekly_capacity_hours,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("", response_model=list[UserResponse], summary="Список пользователей")
async def list_users(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> list[UserResponse]:

    await ensure_can_list_all_tt_users(viewer)
    repo = TimeTrackingUserRepository(session)
    rows = await repo.list_users()
    pos_map = await fetch_auth_user_positions_by_id(authorization or "")
    return [
        _user_response_directory(
            row,
            position=_position_merged(row.position, pos_map, row.auth_user_id),
        )
        for row in rows
    ]


@router.get(
    "/partners",
    response_model=list[UserResponse],
    summary="Список пользователей — только партнёры (должность/роль org)",
)
async def list_partner_users(
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> list[UserResponse]:

    await ensure_can_list_all_tt_users(viewer)
    repo = TimeTrackingUserRepository(session)
    rows = await repo.list_users()
    hints = await fetch_auth_user_partner_hints_by_id(authorization or "")
    pos_map: dict[int, str | None] = {i: d.get("position") for i, d in hints.items()}
    return [
        _user_response_directory(
            row,
            position=_position_merged(row.position, pos_map, row.auth_user_id),
        )
        for row in rows
        if user_satisfies_partner_rule(
            row.position,
            (hints.get(row.auth_user_id) or {}).get("position"),
            (hints.get(row.auth_user_id) or {}).get("role"),
        )
    ]


@router.get(
    "/managed-scope/{manager_auth_user_id}/partners",
    response_model=list[UserResponse],
    summary="Партнёры в зоне видимости менеджера TT",
)
async def list_partner_users_in_manager_scope(
    manager_auth_user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> list[UserResponse]:
    await ensure_managed_scope_allowed(viewer, manager_auth_user_id)
    par = UserProjectAccessRepository(session)
    ur = TimeTrackingUserRepository(session)
    scope_ids = set(await par.list_peer_auth_user_ids_for_manager(manager_auth_user_id))
    rows = await ur.list_users()
    hints = await fetch_auth_user_partner_hints_by_id(authorization or "")
    pos_map: dict[int, str | None] = {i: d.get("position") for i, d in hints.items()}
    out: list[UserResponse] = []
    for row in rows:
        if row.auth_user_id not in scope_ids:
            continue
        h = hints.get(row.auth_user_id) or {}
        if not user_satisfies_partner_rule(row.position, h.get("position"), h.get("role")):
            continue
        out.append(
            _user_response_directory(
                row,
                position=_position_merged(row.position, pos_map, row.auth_user_id),
            )
        )
    return out


@router.get(
    "/managed-scope/{manager_auth_user_id}",
    response_model=list[UserResponse],
    summary="Пользователи в зоне видимости менеджера TT (общие проекты)",
)
async def list_users_in_manager_scope(
    manager_auth_user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> list[UserResponse]:

    await ensure_managed_scope_allowed(viewer, manager_auth_user_id)
    par = UserProjectAccessRepository(session)
    ur = TimeTrackingUserRepository(session)
    scope_ids = set(await par.list_peer_auth_user_ids_for_manager(manager_auth_user_id))
    rows = await ur.list_users()
    pos_map = await fetch_auth_user_positions_by_id(authorization or "")
    out: list[UserResponse] = []
    for row in rows:
        if row.auth_user_id not in scope_ids:
            continue
        out.append(
            _user_response_directory(
                row,
                position=_position_merged(row.position, pos_map, row.auth_user_id),
            )
        )
    return out


@router.get("/{auth_user_id}", response_model=UserResponse, summary="Один пользователь учёта времени")
async def get_user(
    auth_user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> UserResponse:
    await ensure_can_read_tt_user_row(session, viewer, auth_user_id)
    repo = TimeTrackingUserRepository(session)
    row = await repo.get_by_auth_user_id(auth_user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not in time tracking")
    ap = await fetch_auth_user_position(authorization or "", auth_user_id)
    pos = row.position
    if pos is not None and str(pos).strip():
        pos = str(pos).strip()
    elif ap is not None:
        pos = ap
    else:
        pos = None
    return _user_response_directory(row, position=pos)


@router.patch(
    "/{auth_user_id}/weekly-capacity-hours",
    response_model=UserResponse,
    summary="Обновить только норму часов в неделю",
)
async def patch_weekly_capacity(
    auth_user_id: int,
    body: WeeklyCapacityPatchBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> UserResponse:
    await ensure_weekly_capacity_patch_allowed(viewer, auth_user_id)
    repo = TimeTrackingUserRepository(session)
    row = await repo.patch_weekly_capacity_hours(auth_user_id, body.weekly_capacity_hours)
    if not row:
        raise HTTPException(status_code=404, detail="User not in time tracking")
    await session.commit()
    ap = await fetch_auth_user_position(authorization or "", auth_user_id)
    pos = row.position
    if pos is not None and str(pos).strip():
        pos = str(pos).strip()
    elif ap is not None:
        pos = ap
    else:
        pos = None
    return _user_response_directory(row, position=pos)


@router.post("", status_code=200, summary="Создать/обновить пользователя (синхронизация)")
async def upsert_user(
    body: UserUpsertBody,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> dict:

    ensure_upsert_user_allowed(viewer, body.auth_user_id)
    repo = TimeTrackingUserRepository(session)
    payload = body.model_dump(exclude_unset=True)
    update_position = "position" in payload
    try:
        await repo.upsert_user(
            auth_user_id=body.auth_user_id,
            email=body.email,
            display_name=body.display_name,
            picture=body.picture,
            role=body.role,
            is_blocked=body.is_blocked,
            is_archived=body.is_archived,
            weekly_capacity_hours=body.weekly_capacity_hours,
            position=body.position,
            update_position=update_position,
        )
        await session.commit()
    except ProgrammingError as e:
        await session.rollback()
        orig = getattr(e, "orig", None)
        hint = (
            "Проверьте, что в БД применён скрипт scripts/add_time_tracking_team_workload.sql "
            "(колонка weekly_capacity_hours и таблица time_tracking_entries)."
        )
        raise HTTPException(
            status_code=503,
            detail=f"{hint} Ошибка СУБД: {orig or e}",
        ) from e
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(status_code=409, detail=str(getattr(e, "orig", None) or e)) from e
    except DBAPIError as e:
        await session.rollback()
        raise HTTPException(status_code=503, detail=str(getattr(e, "orig", None) or e)) from e
    return {"ok": True}


@router.delete("/{auth_user_id}", status_code=200, summary="Удалить пользователя из списка")
async def delete_user(
    auth_user_id: int,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
    authorization: str | None = Header(None, alias="Authorization"),
) -> dict:

    ensure_delete_tt_user_allowed(viewer)
    par = UserProjectAccessRepository(session)
    cpr = ClientProjectRepository(session)
    affected_before = set(await par.list_project_ids(auth_user_id))
    repo = TimeTrackingUserRepository(session)
    deleted = await repo.delete_by_auth_user_id(auth_user_id)
    if not deleted:
        await session.rollback()
        return {"ok": True, "deleted": False}
    await session.flush()
    try:
        await ensure_projects_have_partner_assignee(
            session,
            par,
            affected_before,
            projects=cpr,
            authorization=authorization,
        )
    except ValueError as e:
        await session.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    return {"ok": True, "deleted": True}
