

from __future__ import annotations

from datetime import date

import uuid
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import WeeklyTimeSubmissionModel
from infrastructure.repository_shared import _now_utc

_STATUS_LOCKED = "submitted"


class WeeklySubmissionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def is_work_date_locked(self, auth_user_id: int, work_date: date) -> bool:
        q = select(WeeklyTimeSubmissionModel.id).where(
            and_(
                WeeklyTimeSubmissionModel.auth_user_id == auth_user_id,
                WeeklyTimeSubmissionModel.week_start <= work_date,
                WeeklyTimeSubmissionModel.week_end >= work_date,
                WeeklyTimeSubmissionModel.status == _STATUS_LOCKED,
            )
        )
        r = await self._session.execute(q)
        return r.first() is not None

    async def upsert_submission(
        self,
        *,
        auth_user_id: int,
        week_start: date,
        week_end: date,
        auto: bool,
    ) -> WeeklyTimeSubmissionModel:
        q = select(WeeklyTimeSubmissionModel).where(
            and_(
                WeeklyTimeSubmissionModel.auth_user_id == auth_user_id,
                WeeklyTimeSubmissionModel.week_start == week_start,
            )
        )
        r = await self._session.execute(q)
        row = r.scalars().one_or_none()
        now = _now_utc()
        if row:
            return row
        m = WeeklyTimeSubmissionModel(
            id=str(uuid.uuid4()),
            auth_user_id=auth_user_id,
            week_start=week_start,
            week_end=week_end,
            status=_STATUS_LOCKED,
            auto_submitted_at=now if auto else None,
            created_at=now,
            updated_at=now,
        )
        self._session.add(m)
        return m
