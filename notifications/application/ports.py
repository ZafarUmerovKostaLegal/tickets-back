from abc import ABC, abstractmethod
from typing import Optional, Sequence
from domain.entities import Notification


class HealthRepositoryPort(ABC):
    @abstractmethod
    async def check(self) -> bool:
        pass


class NotificationFilters:
    def __init__(self, skip: int = 0, limit: int = 50, include_archived: bool = False):
        self.skip = skip
        self.limit = limit
        self.include_archived = include_archived


class NotificationRepositoryPort(ABC):
    @abstractmethod
    async def create(
        self,
        uuid: str,
        title: str,
        description: str,
        photo_path: Optional[str] = None,
    ) -> Notification:
        pass

    @abstractmethod
    async def get_by_uuid(self, notification_uuid: str) -> Optional[Notification]:
        pass

    @abstractmethod
    async def get_all(self, filters: NotificationFilters) -> Sequence[Notification]:
        pass

    @abstractmethod
    async def update(
        self,
        notification_uuid: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        photo_path: Optional[str] = None,
    ) -> Optional[Notification]:
        pass

    @abstractmethod
    async def set_archived(self, notification_uuid: str, is_archived: bool) -> Optional[Notification]:
        pass

    @abstractmethod
    async def delete(self, notification_uuid: str) -> bool:
        pass
