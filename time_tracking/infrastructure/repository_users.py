from __future__ import annotations

from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.manual_tt_users import is_manual_tt_auth_user_id
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

    async def get_by_email(self, email: str) -> TimeTrackingUserModel | None:
        normalized = (email or "").strip().lower()
        if not normalized:
            return None
        r = await self._session.execute(
            select(TimeTrackingUserModel).where(
                func.lower(TimeTrackingUserModel.email) == normalized
            )
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
        manual = is_manual_tt_auth_user_id(int(auth_user_id))
        if row:
            if manual:
                row.email = email
                row.display_name = display_name
                row.picture = picture
                if update_position:
                    row.position = pos_norm
            # Non-manual: membership hub only — do not dual-write auth PII.
            row.role = role
            row.is_blocked = is_blocked
            row.is_archived = is_archived
            if weekly_capacity_hours is not None:
                row.weekly_capacity_hours = weekly_capacity_hours
            row.updated_at = now
            self._session.add(row)
            return row

        cap = weekly_capacity_hours if weekly_capacity_hours is not None else Decimal("35")
        row = TimeTrackingUserModel(
            auth_user_id=auth_user_id,
            email=email,
            display_name=display_name if manual else None,
            picture=picture if manual else None,
            position=pos_norm if update_position and manual else None,
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

    async def patch_can_transfer_time_without_project_access(
        self,
        auth_user_id: int,
        *,
        enabled: bool,
    ) -> TimeTrackingUserModel | None:
        row = await self.get_by_auth_user_id(auth_user_id)
        if not row:
            return None
        row.can_transfer_time_without_project_access = bool(enabled)
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def patch_lifecycle_flags(
        self,
        auth_user_id: int,
        *,
        is_blocked: bool,
        is_archived: bool,
    ) -> TimeTrackingUserModel | None:
        row = await self.get_by_auth_user_id(auth_user_id)
        if not row:
            return None
        row.is_blocked = bool(is_blocked)
        row.is_archived = bool(is_archived)
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def patch_auth_profile(
        self,
        auth_user_id: int,
        *,
        email: str,
        display_name: str | None,
        picture: str | None,
        role: str,
        is_blocked: bool,
        is_archived: bool,
        position: str | None = None,
        update_position: bool = False,
    ) -> TimeTrackingUserModel | None:
        row = await self.get_by_auth_user_id(auth_user_id)
        if not row:
            return None
        # Real auth users: role + lifecycle only. Manual TT users keep local PII writes.
        if is_manual_tt_auth_user_id(int(auth_user_id)):
            row.email = email
            row.display_name = display_name
            row.picture = picture
            if update_position:
                row.position = (position or "").strip() or None
        row.role = role
        row.is_blocked = bool(is_blocked)
        row.is_archived = bool(is_archived)
        row.updated_at = _now_utc()
        self._session.add(row)
        return row

    async def delete_by_auth_user_id(self, auth_user_id: int) -> bool:
        r = await self._session.execute(
            delete(TimeTrackingUserModel).where(TimeTrackingUserModel.auth_user_id == auth_user_id)
        )
        return r.rowcount > 0
