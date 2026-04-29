import uuid as uuid_lib
from typing import Optional, Sequence
from domain.entities import Notification
from application.ports import HealthRepositoryPort, NotificationRepositoryPort, NotificationFilters


class GetHealthUseCase:
    def __init__(self, health_repo: HealthRepositoryPort):
        self._health_repo = health_repo

    async def execute(self, service_name: str):
        from datetime import datetime
        from domain.entities import HealthEntity
        db_ok = await self._health_repo.check()
        status = "healthy" if db_ok else "degraded"
        return HealthEntity(
            status=status,
            service=service_name,
            timestamp=datetime.utcnow(),
        )


class CreateNotificationUseCase:
    def __init__(self, repo: NotificationRepositoryPort):
        self._repo = repo

    async def execute(
        self,
        title: str,
        description: str,
        photo_path: Optional[str] = None,
    ) -> Notification:
        notification_uuid = str(uuid_lib.uuid4())
        return await self._repo.create(
            uuid=notification_uuid,
            title=title,
            description=description,
            photo_path=photo_path,
        )


class GetNotificationUseCase:
    def __init__(self, repo: NotificationRepositoryPort):
        self._repo = repo

    async def execute(self, notification_uuid: str) -> Optional[Notification]:
        return await self._repo.get_by_uuid(notification_uuid)


class ListNotificationsUseCase:
    def __init__(self, repo: NotificationRepositoryPort):
        self._repo = repo

    async def execute(self, filters: NotificationFilters) -> Sequence[Notification]:
        return await self._repo.get_all(filters)


class UpdateNotificationUseCase:
    def __init__(self, repo: NotificationRepositoryPort):
        self._repo = repo

    async def execute(
        self,
        notification_uuid: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        photo_path: Optional[str] = None,
    ) -> Optional[Notification]:
        return await self._repo.update(
            notification_uuid=notification_uuid,
            title=title,
            description=description,
            photo_path=photo_path,
        )


class ArchiveNotificationUseCase:
    def __init__(self, repo: NotificationRepositoryPort):
        self._repo = repo

    async def execute(self, notification_uuid: str, is_archived: bool = True) -> Optional[Notification]:
        return await self._repo.set_archived(notification_uuid=notification_uuid, is_archived=is_archived)


class DeleteNotificationUseCase:
    def __init__(self, repo: NotificationRepositoryPort):
        self._repo = repo

    async def execute(self, notification_uuid: str) -> bool:
        return await self._repo.delete(notification_uuid)
