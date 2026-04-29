from typing import Optional, Sequence
from sqlalchemy import select, text, and_
from sqlalchemy.ext.asyncio import AsyncSession
from domain.entities import Notification
from application.ports import (
    HealthRepositoryPort,
    NotificationRepositoryPort,
    NotificationFilters,
)
from infrastructure.models import NotificationModel


class HealthRepository(HealthRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class NotificationRepository(NotificationRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_entity(self, m: NotificationModel) -> Notification:
        return Notification(
            id=m.id,
            uuid=m.uuid,
            title=m.title,
            description=m.description,
            photo_path=m.photo_path,
            is_archived=m.is_archived,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    async def create(
        self,
        uuid: str,
        title: str,
        description: str,
        photo_path: Optional[str] = None,
    ) -> Notification:
        model = NotificationModel(
            uuid=uuid,
            title=title,
            description=description,
            photo_path=photo_path,
            is_archived=False,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_uuid(self, notification_uuid: str) -> Optional[Notification]:
        result = await self._session.execute(
            select(NotificationModel).where(NotificationModel.uuid == notification_uuid)
        )
        row = result.scalars().one_or_none()
        return self._to_entity(row) if row else None

    async def get_all(self, filters: NotificationFilters) -> Sequence[Notification]:
        q = select(NotificationModel).order_by(NotificationModel.created_at.desc())
        conditions = []
        if not filters.include_archived:
            conditions.append(NotificationModel.is_archived == False)
        if conditions:
            q = q.where(and_(*conditions))
        q = q.offset(filters.skip).limit(filters.limit)
        result = await self._session.execute(q)
        rows = result.scalars().all()
        return [self._to_entity(r) for r in rows]

    async def update(
        self,
        notification_uuid: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        photo_path: Optional[str] = None,
    ) -> Optional[Notification]:
        result = await self._session.execute(
            select(NotificationModel).where(NotificationModel.uuid == notification_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return None
        if title is not None:
            model.title = title
        if description is not None:
            model.description = description
        if photo_path is not None:
            model.photo_path = photo_path
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def set_archived(self, notification_uuid: str, is_archived: bool) -> Optional[Notification]:
        result = await self._session.execute(
            select(NotificationModel).where(NotificationModel.uuid == notification_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return None
        model.is_archived = is_archived
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def delete(self, notification_uuid: str) -> bool:
        result = await self._session.execute(
            select(NotificationModel).where(NotificationModel.uuid == notification_uuid)
        )
        model = result.scalars().one_or_none()
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
