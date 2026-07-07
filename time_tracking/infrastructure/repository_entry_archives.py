from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeEntryArchiveModel
from infrastructure.repository_shared import _now_utc


class TimeEntryArchiveRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        time_entry_id: str,
        auth_user_id: int,
        project_id: str | None,
        client_id: str | None,
        duplicate_group_id: str | None,
        archived_by_auth_user_id: int,
        payload: dict[str, Any],
    ) -> TimeEntryArchiveModel:
        row = TimeEntryArchiveModel(
            id=str(uuid.uuid4()),
            time_entry_id=time_entry_id,
            auth_user_id=int(auth_user_id),
            project_id=project_id,
            client_id=client_id,
            duplicate_group_id=duplicate_group_id,
            archived_at=_now_utc(),
            archived_by_auth_user_id=int(archived_by_auth_user_id),
            restored_at=None,
            restored_by_auth_user_id=None,
            payload=json.dumps(payload, default=str, ensure_ascii=False),
        )
        self._session.add(row)
        return row

    async def get_by_id(self, archive_id: str) -> TimeEntryArchiveModel | None:
        r = await self._session.execute(
            select(TimeEntryArchiveModel).where(TimeEntryArchiveModel.id == archive_id)
        )
        return r.scalars().one_or_none()

    async def list_for_project(
        self,
        project_id: str,
        *,
        include_restored: bool = False,
    ) -> list[TimeEntryArchiveModel]:
        q = select(TimeEntryArchiveModel).where(
            TimeEntryArchiveModel.project_id == project_id,
        )
        if not include_restored:
            q = q.where(TimeEntryArchiveModel.restored_at.is_(None))
        q = q.order_by(TimeEntryArchiveModel.archived_at.desc())
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def mark_restored(
        self,
        archive_id: str,
        *,
        restored_by_auth_user_id: int,
    ) -> TimeEntryArchiveModel | None:
        row = await self.get_by_id(archive_id)
        if not row or row.restored_at is not None:
            return None
        row.restored_at = _now_utc()
        row.restored_by_auth_user_id = int(restored_by_auth_user_id)
        self._session.add(row)
        return row

    @staticmethod
    def payload_dict(row: TimeEntryArchiveModel) -> dict[str, Any]:
        try:
            data = json.loads(row.payload or "{}")
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def to_api(row: TimeEntryArchiveModel) -> dict[str, Any]:
        payload = TimeEntryArchiveRepository.payload_dict(row)
        return {
            "archive_id": row.id,
            "time_entry_id": row.time_entry_id,
            "auth_user_id": row.auth_user_id,
            "project_id": row.project_id,
            "client_id": row.client_id,
            "duplicate_group_id": row.duplicate_group_id,
            "archived_at": row.archived_at.isoformat() if row.archived_at else None,
            "archived_by_auth_user_id": row.archived_by_auth_user_id,
            "restored_at": row.restored_at.isoformat() if row.restored_at else None,
            "restored_by_auth_user_id": row.restored_by_auth_user_id,
            "is_restored": row.restored_at is not None,
            **payload,
        }
