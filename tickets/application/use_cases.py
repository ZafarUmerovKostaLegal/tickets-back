import uuid as uuid_lib
from typing import Optional, Sequence
from domain.entities import Ticket, Comment, Status
from application.ports import HealthRepositoryPort, TicketRepositoryPort, CommentRepositoryPort, TicketFilters


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


class CreateTicketUseCase:
    def __init__(self, ticket_repo: TicketRepositoryPort):
        self._ticket_repo = ticket_repo

    async def execute(
        self,
        theme: str,
        description: str,
        attachment_path: Optional[str],
        created_by_user_id: int,
        category: str,
        priority: str,
    ) -> Ticket:
        ticket_uuid = str(uuid_lib.uuid4())
        return await self._ticket_repo.create(
            uuid=ticket_uuid,
            theme=theme,
            description=description,
            attachment_path=attachment_path,
            status=Status.OPEN.value,
            created_by_user_id=created_by_user_id,
            category=category,
            priority=priority,
        )


class GetTicketUseCase:
    def __init__(self, ticket_repo: TicketRepositoryPort):
        self._ticket_repo = ticket_repo

    async def execute(self, ticket_uuid: str) -> Optional[Ticket]:
        return await self._ticket_repo.get_by_uuid(ticket_uuid)


class ListTicketsUseCase:
    def __init__(self, ticket_repo: TicketRepositoryPort):
        self._ticket_repo = ticket_repo

    async def execute(self, filters: TicketFilters) -> list[Ticket]:
        return list(await self._ticket_repo.get_all(filters))


class UpdateTicketUseCase:
    def __init__(self, ticket_repo: TicketRepositoryPort):
        self._ticket_repo = ticket_repo

    async def execute(
        self,
        ticket_uuid: str,
        theme: Optional[str] = None,
        description: Optional[str] = None,
        attachment_path: Optional[str] = None,
        status: Optional[str] = None,
        category: Optional[str] = None,
        priority: Optional[str] = None,
    ) -> Optional[Ticket]:
        return await self._ticket_repo.update(
            ticket_uuid=ticket_uuid,
            theme=theme,
            description=description,
            attachment_path=attachment_path,
            status=status,
            category=category,
            priority=priority,
        )


class ArchiveTicketUseCase:
    def __init__(self, ticket_repo: TicketRepositoryPort):
        self._ticket_repo = ticket_repo

    async def execute(self, ticket_uuid: str, is_archived: bool = True) -> Optional[Ticket]:
        return await self._ticket_repo.set_archived(ticket_uuid=ticket_uuid, is_archived=is_archived)


class CreateCommentUseCase:
    def __init__(self, comment_repo: CommentRepositoryPort):
        self._comment_repo = comment_repo

    async def execute(self, ticket_id: int, user_id: int, content: str) -> Comment:
        return await self._comment_repo.create(ticket_id=ticket_id, user_id=user_id, content=content)


class ListCommentsUseCase:
    def __init__(self, comment_repo: CommentRepositoryPort):
        self._comment_repo = comment_repo

    async def execute(self, ticket_id: int) -> Sequence[Comment]:
        return await self._comment_repo.get_by_ticket(ticket_id)


class UpdateCommentUseCase:
    def __init__(self, comment_repo: CommentRepositoryPort):
        self._comment_repo = comment_repo

    async def execute(self, comment_id: int, content: str) -> Optional[Comment]:
        return await self._comment_repo.update(comment_id=comment_id, content=content)


class DeleteCommentUseCase:
    def __init__(self, comment_repo: CommentRepositoryPort):
        self._comment_repo = comment_repo

    async def execute(self, comment_id: int) -> bool:
        return await self._comment_repo.delete(comment_id)
