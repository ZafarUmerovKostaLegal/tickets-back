

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeEntryEditUnlockModel
from infrastructure.repository_shared import _now_utc


class TimeEntryEditUnlockRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def is_active_unlock(self, auth_user_id: int, work_date: date) -> bool:
        now = _now_utc()
        q = select(TimeEntryEditUnlockModel.id).where(
            and_(
                TimeEntryEditUnlockModel.auth_user_id == auth_user_id,
                TimeEntryEditUnlockModel.work_date == work_date,
                TimeEntryEditUnlockModel.expires_at > now,
            )
        )
        r = await self._session.execute(q)
        return r.first() is not None

    async def upsert_unlock(
        self,
        *,
        auth_user_id: int,
        work_date: date,
        granted_by_auth_user_id: int,
        duration_hours: int = 24,
    ) -> TimeEntryEditUnlockModel:
        now = _now_utc()
        expires_at = now + timedelta(hours=duration_hours)
        table = TimeEntryEditUnlockModel.__table__
        ins = insert(table).values(
            id=str(uuid.uuid4()),
            auth_user_id=auth_user_id,
            work_date=work_date,
            granted_by_auth_user_id=granted_by_auth_user_id,
            expires_at=expires_at,
            created_at=now,
        )
        stmt = ins.on_conflict_do_update(
            constraint="uq_tt_te_unlock_user_day",
            set_={
                "expires_at": expires_at,
                "granted_by_auth_user_id": granted_by_auth_user_id,
            },
        )
        await self._session.execute(stmt)
        q = select(TimeEntryEditUnlockModel).where(
            and_(
                TimeEntryEditUnlockModel.auth_user_id == auth_user_id,
                TimeEntryEditUnlockModel.work_date == work_date,
            )
        )
        r = await self._session.execute(q)
        row = r.scalars().one()
        return row
