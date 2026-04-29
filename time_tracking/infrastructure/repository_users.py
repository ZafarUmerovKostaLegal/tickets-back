from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeTrackingUserModel
from infrastructure.repository_shared import _now_utc


class TimeTrackingUserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_users(self) -> list[TimeTrackingUserModel]:
        q = select(TimeTrackingUserModel).order_by(TimeTrackingUserModel.id)
        r = await self._session.execute(q)
        return list(r.scalars().all())

    async def get_by_auth_user_id(self, auth_user_id: int) -> TimeTrackingUserModel | None:
        r = await self._session.execute(
            select(TimeTrackingUserModel).where(TimeTrackingUserModel.auth_user_id == auth_user_id)
        )
        return r.scalars().one_or_none()

    async def list_by_auth_user_ids(self, auth_user_ids: list[int]) -> list[TimeTrackingUserModel]:
        if not auth_user_ids:
            return []
        r = await self._session.execute(
            select(TimeTrackingUserModel).where(
                TimeTrackingUserModel.auth_user_id.in_(auth_user_ids)
            )
        )
        return list(r.scalars().all())

    async def upsert_user(
        self,
        *,
        auth_user_id: int,
        email: str,
        display_name: str | None = None,
        picture: str | None = None,
        role: str = "",
        is_blocked: bool = False,
        is_archived: bool = False,
        weekly_capacity_hours: Decimal | None = None,
        position: str | None = None,
        update_position: bool = False,
    ) -> TimeTrackingUserModel:
        row = await self.get_by_auth_user_id(auth_user_id)
        now = _now_utc()
        pos_norm = (position or "").strip() or None if update_position else None
        if row:
            row.email = email
            row.display_name = display_name
            row.picture = picture
            row.role = role
            row.is_blocked = is_blocked
            row.is_archived = is_archived
            if weekly_capacity_hours is not None:
                row.weekly_capacity_hours = weekly_capacity_hours
            if update_position:
                row.position = pos_norm
            row.updated_at = now
            self._session.add(row)
            return row

        cap = weekly_capacity_hours if weekly_capacity_hours is not None else Decimal("35")
        row = TimeTrackingUserModel(
            auth_user_id=auth_user_id,
            email=email,
            display_name=display_name,
            picture=picture,
            position=pos_norm if update_position else None,
            role=role,
            is_blocked=is_blocked,
            is_archived=is_archived,
            weekly_capacity_hours=cap,
            created_at=now,
            updated_at=None,
        )
        self._session.add(row)
        return row

    async def patch_weekly_capacity_hours(
        self,
        auth_user_id: int,
        weekly_capacity_hours: Decimal,
    ) -> TimeTrackingUserModel | None:
        row = await self.get_by_auth_user_id(auth_user_id)
        if not row:
            return None
        row.weekly_capacity_hours = weekly_capacity_hours
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete_by_auth_user_id(self, auth_user_id: int) -> bool:
        r = await self._session.execute(
            delete(TimeTrackingUserModel).where(TimeTrackingUserModel.auth_user_id == auth_user_id)
        )
        return r.rowcount > 0
