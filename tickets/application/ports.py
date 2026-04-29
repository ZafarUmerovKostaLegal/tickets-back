from abc import ABC, abstractmethod
from typing import Optional, Sequence
from domain.entities import Ticket, Comment


class HealthRepositoryPort(ABC):
    @abstractmethod
    async def check(self) -> bool:
        pass


class TicketFilters:
    def __init__(
        self,
        skip: int = 0,
        limit: int = 50,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        category: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
        include_archived: bool = False,
    ):
        self.skip = skip
        self.limit = limit
        self.status = status
        self.priority = priority
        self.category = category
        self.created_by_user_id = created_by_user_id
        self.include_archived = include_archived


class TicketRepositoryPort(ABC):
    @abstractmethod
    async def create(
        self,
        uuid: str,
        theme: str,
        description: str,
        attachment_path: Optional[str],
        status: str,
        created_by_user_id: int,
        category: str,
        priority: str,
    ) -> Ticket:
        pass

    @abstractmethod
    async def get_by_uuid(self, ticket_uuid: str) -> Optional[Ticket]:
        pass

    @abstractmethod
    async def get_all(self, filters: TicketFilters) -> Sequence[Ticket]:
        pass

    @abstractmethod
    async def update(
        self,
        ticket_uuid: str,
        theme: Optional[str] = None,
        description: Optional[str] = None,
        attachment_path: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> Optional[Ticket]:
        pass

    @abstractmethod
    async def set_archived(self, ticket_uuid: str, is_archived: bool) -> Optional[Ticket]:
        pass


class CommentRepositoryPort(ABC):
    @abstractmethod
    async def create(self, ticket_id: int, user_id: int, content: str) -> Comment:
        pass

    @abstractmethod
    async def get_by_ticket(self, ticket_id: int) -> Sequence[Comment]:
        pass

    @abstractmethod
    async def get_by_id(self, comment_id: int) -> Optional[Comment]:
        pass

    @abstractmethod
    async def update(self, comment_id: int, content: str) -> Optional[Comment]:
        pass

    @abstractmethod
    async def delete(self, comment_id: int) -> bool:
        pass
