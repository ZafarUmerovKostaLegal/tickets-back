from __future__ import annotations

import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeTrackingUserProjectAccessModel
from infrastructure.repository_clients import ClientProjectRepository
from infrastructure.repository_shared import _now_utc


class UserProjectAccessRepository:


    def __init__(self, session: AsyncSession):
        self._session = session

    async def grant_access_if_absent(
        self,
        auth_user_id: int,
        project_id: str,
        *,
        granted_by_auth_user_id: int | None,
        projects: ClientProjectRepository,
    ) -> None:

        pid = (project_id or "").strip()
        if not pid:
            raise ValueError("Пустой идентификатор проекта")
        if await projects.get_by_id_global(pid) is None:
            raise ValueError(f"Проект не найден: {pid}")
        if await self.has_access(auth_user_id, pid):
            return
        self._session.add(
            TimeTrackingUserProjectAccessModel(
                id=str(uuid.uuid4()),
                auth_user_id=auth_user_id,
                project_id=pid,
                granted_by_auth_user_id=granted_by_auth_user_id,
                created_at=_now_utc(),
            )
        )

    async def has_access(self, auth_user_id: int, project_id: str) -> bool:
        r = await self._session.execute(
            select(TimeTrackingUserProjectAccessModel.id).where(
                TimeTrackingUserProjectAccessModel.auth_user_id == auth_user_id,
                TimeTrackingUserProjectAccessModel.project_id == project_id,
            )
        )
        return r.scalar_one_or_none() is not None

    async def list_project_ids(self, auth_user_id: int) -> list[str]:
        r = await self._session.execute(
            select(TimeTrackingUserProjectAccessModel.project_id)
            .where(TimeTrackingUserProjectAccessModel.auth_user_id == auth_user_id)
            .order_by(TimeTrackingUserProjectAccessModel.project_id.asc())
        )
        return [str(x) for x in r.scalars().all()]

    async def list_auth_user_ids_for_project(self, project_id: str) -> list[int]:
        r = await self._session.execute(
            select(TimeTrackingUserProjectAccessModel.auth_user_id).where(
                TimeTrackingUserProjectAccessModel.project_id == project_id,
            )
        )
        return [int(x) for x in r.scalars().all()]

    async def list_peer_auth_user_ids_for_manager(self, manager_auth_user_id: int) -> list[int]:

        my_projects = await self.list_project_ids(manager_auth_user_id)
        ids: set[int] = {int(manager_auth_user_id)}
        for pid in my_projects:
            for uid in await self.list_auth_user_ids_for_project(pid):
                ids.add(int(uid))
        return sorted(ids)

    async def replace_all(
        self,
        auth_user_id: int,
        project_ids: list[str],
        *,
        granted_by_auth_user_id: int | None,
        projects: ClientProjectRepository,
    ) -> set[str]:

        old_pids = await self.list_project_ids(auth_user_id)
        seen: set[str] = set()
        normalized: list[str] = []
        for raw in project_ids:
            pid = (raw or "").strip()
            if not pid or pid in seen:
                continue
            seen.add(pid)
            if await projects.get_by_id_global(pid) is None:
                raise ValueError(f"Проект не найден: {pid}")
            normalized.append(pid)

        await self._session.execute(
            delete(TimeTrackingUserProjectAccessModel).where(
                TimeTrackingUserProjectAccessModel.auth_user_id == auth_user_id
            )
        )
        now = _now_utc()
        for pid in normalized:
            self._session.add(
                TimeTrackingUserProjectAccessModel(
                    id=str(uuid.uuid4()),
                    auth_user_id=auth_user_id,
                    project_id=pid,
                    granted_by_auth_user_id=granted_by_auth_user_id,
                    created_at=now,
                )
            )
        await self._session.flush()
        return set(old_pids) | set(normalized)
