

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.repositories import (
    ClientProjectRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)


_VIEW_ROLES_TIME_ENTRIES = frozenset(
    {
        "Главный администратор",
        "Администратор",
        "Партнер",
        "IT отдел",
        "Офис менеджер",
    }
)
_MANAGE_ROLES_TIME_ENTRIES = frozenset({"Главный администратор", "Администратор", "Партнер"})


async def viewer_can_bypass_work_week_submission_lock(
    session: AsyncSession,
    viewer: dict,
) -> bool:

    if _org_role(viewer) in _MANAGE_ROLES_TIME_ENTRIES:
        return True
    ur = TimeTrackingUserRepository(session)
    row = await ur.get_by_auth_user_id(_viewer_id(viewer))
    return bool(row and (row.role or "").strip() == "manager")


def _org_role(viewer: dict) -> str:
    return (viewer.get("role") or "").strip()


def _viewer_id(viewer: dict) -> int:
    uid = viewer.get("id")
    if uid is None:
        raise HTTPException(status_code=403, detail="В токене нет id пользователя")
    return int(uid)


async def ensure_time_entry_subject_allowed(
    session: AsyncSession,
    viewer: dict,
    target_auth_user_id: int,
    *,
    write: bool,
) -> None:

    if _viewer_id(viewer) == target_auth_user_id:
        return
    role = _org_role(viewer)
    if write:
        if role in _MANAGE_ROLES_TIME_ENTRIES:
            return
    else:
        if role in _VIEW_ROLES_TIME_ENTRIES:
            return

    ur = TimeTrackingUserRepository(session)
    row = await ur.get_by_auth_user_id(_viewer_id(viewer))
    tt_role = (row.role or "").strip() if row else ""
    if tt_role != "manager":
        raise HTTPException(
            status_code=403,
            detail=(
                "Можно изменять только свои записи времени либо нужны права администратора или менеджера учёта времени"
                if write
                else "Можно просматривать только свои записи времени либо нужна роль офиса или менеджера учёта времени"
            ),
        )

    par = UserProjectAccessRepository(session)
    scope = set(await par.list_peer_auth_user_ids_for_manager(_viewer_id(viewer)))
    if target_auth_user_id in scope:
        return
    raise HTTPException(
        status_code=403,
        detail=(
            "Менеджер учёта времени может менять записи только сотрудников с общими проектами доступа"
            if write
            else "Менеджер учёта времени видит записи только сотрудников с общими проектами доступа"
        ),
    )


async def ensure_can_read_tt_user_row(
    session: AsyncSession,
    viewer: dict,
    target_auth_user_id: int,
) -> None:

    if _viewer_id(viewer) == target_auth_user_id:
        return
    role = _org_role(viewer)
    if role in _VIEW_ROLES_TIME_ENTRIES:
        return
    ur = TimeTrackingUserRepository(session)
    row = await ur.get_by_auth_user_id(_viewer_id(viewer))
    tt_role = (row.role or "").strip() if row else ""
    if tt_role != "manager":
        raise HTTPException(status_code=403, detail="Недостаточно прав для просмотра профиля другого пользователя")
    par = UserProjectAccessRepository(session)
    scope = set(await par.list_peer_auth_user_ids_for_manager(_viewer_id(viewer)))
    if target_auth_user_id in scope:
        return
    raise HTTPException(status_code=403, detail="Менеджер видит только пользователей с общими проектами доступа")


async def ensure_can_list_all_tt_users(viewer: dict) -> None:

    role = _org_role(viewer)
    if role in _VIEW_ROLES_TIME_ENTRIES:
        return
    raise HTTPException(
        status_code=403,
        detail="Полный список пользователей учёта времени доступен только офису и администраторам",
    )


async def ensure_managed_scope_allowed(viewer: dict, manager_auth_user_id: int) -> None:

    if _viewer_id(viewer) == manager_auth_user_id:
        return
    if _org_role(viewer) in _VIEW_ROLES_TIME_ENTRIES:
        return
    raise HTTPException(status_code=403, detail="Недостаточно прав для зоны видимости менеджера")


async def ensure_weekly_capacity_patch_allowed(viewer: dict, target_auth_user_id: int) -> None:

    if _viewer_id(viewer) == target_auth_user_id:
        return
    if _org_role(viewer) in _MANAGE_ROLES_TIME_ENTRIES:
        return
    raise HTTPException(
        status_code=403,
        detail="Норму часов может менять только сам пользователь или администратор",
    )


def ensure_upsert_user_allowed(viewer: dict, body_auth_user_id: int) -> None:

    if _viewer_id(viewer) == body_auth_user_id:
        return
    if _org_role(viewer) in _MANAGE_ROLES_TIME_ENTRIES:
        return
    raise HTTPException(
        status_code=403,
        detail="Синхронизировать другого пользователя могут только администратор или партнёр",
    )


def ensure_delete_tt_user_allowed(viewer: dict) -> None:
    if _org_role(viewer) in _MANAGE_ROLES_TIME_ENTRIES:
        return
    raise HTTPException(status_code=403, detail="Удаление из учёта времени — только для администраторов")


async def ensure_can_list_project_assignees(
    session: AsyncSession,
    viewer: dict,
    project_id: str,
) -> None:

    pid = (project_id or "").strip()
    if not pid:
        raise HTTPException(status_code=400, detail="Пустой идентификатор проекта")
    cpr = ClientProjectRepository(session)
    if await cpr.get_by_id_global(pid) is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    role = _org_role(viewer)
    if role in _VIEW_ROLES_TIME_ENTRIES:
        return
    par = UserProjectAccessRepository(session)
    viewer_id = _viewer_id(viewer)
    if await par.has_access(viewer_id, pid):
        return
    ur = TimeTrackingUserRepository(session)
    row = await ur.get_by_auth_user_id(viewer_id)
    tt_role = (row.role or "").strip() if row else ""
    if tt_role == "manager":
        assignee_ids = await par.list_auth_user_ids_for_project(pid)
        peers = set(await par.list_peer_auth_user_ids_for_manager(viewer_id))
        if any(uid in peers for uid in assignee_ids):
            return
    raise HTTPException(
        status_code=403,
        detail="Нет прав на просмотр списка назначенных на проект (нужен доступ к проекту или роль офиса)",
    )
