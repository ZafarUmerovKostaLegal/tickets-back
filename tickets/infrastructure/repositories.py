from typing import Optional, Sequence
from sqlalchemy import select, text, and_
from sqlalchemy.ext.asyncio import AsyncSession
from domain.entities import Ticket, Comment
from application.ports import HealthRepositoryPort, TicketRepositoryPort, CommentRepositoryPort, TicketFilters
from infrastructure.models import TicketModel, CommentModel


class HealthRepository(HealthRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class TicketRepository(TicketRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_entity(self, m: TicketModel) -> Ticket:
        return Ticket(
            id=m.id,
            uuid=m.uuid,
            theme=m.theme,
            description=m.description,
            attachment_path=m.attachment_path,
            status=m.status,
            created_by_user_id=m.created_by_user_id,
            created_at=m.created_at,
            category=m.category,
            priority=m.priority,
            is_archived=getattr(m, "is_archived", False),
        )

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
        model = TicketModel(
            uuid=uuid,
            theme=theme,
            description=description,
            attachment_path=attachment_path,
            status=status,
            created_by_user_id=created_by_user_id,
            category=category,
            priority=priority,
            is_archived=False,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_uuid(self, ticket_uuid: str) -> Optional[Ticket]:
        result = await self._session.execute(select(TicketModel).where(TicketModel.uuid == ticket_uuid))
        row = result.scalars().one_or_none()
        return self._to_entity(row) if row else None

    async def get_by_internal_id(self, ticket_id: int) -> Optional[Ticket]:
        result = await self._session.execute(select(TicketModel).where(TicketModel.id == ticket_id))
        row = result.scalars().one_or_none()
        return self._to_entity(row) if row else None

    async def get_all(self, filters: TicketFilters) -> Sequence[Ticket]:
        q = select(TicketModel).order_by(TicketModel.created_at.desc())
        conditions = []
        if not filters.include_archived:
            conditions.append(TicketModel.is_archived == False)
        if filters.status is not None:
            conditions.append(TicketModel.status == filters.status)
        if filters.priority is not None:
            conditions.append(TicketModel.priority == filters.priority)
        if filters.category is not None:
            conditions.append(TicketModel.category == filters.category)
        if filters.created_by_user_id is not None:
            conditions.append(TicketModel.created_by_user_id == filters.created_by_user_id)
        if conditions:
            q = q.where(and_(*conditions))
        q = q.offset(filters.skip).limit(filters.limit)
        result = await self._session.execute(q)
        rows = result.scalars().all()
        return [self._to_entity(r) for r in rows]

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
        result = await self._session.execute(select(TicketModel).where(TicketModel.uuid == ticket_uuid))
        model = result.scalars().one_or_none()
        if not model:
            return None
        if theme is not None:
            model.theme = theme
        if description is not None:
            model.description = description
        if attachment_path is not None:
            model.attachment_path = attachment_path
        if status is not None:
            model.status = status
        if category is not None:
            model.category = category
        if priority is not None:
            model.priority = priority
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def set_archived(self, ticket_uuid: str, is_archived: bool) -> Optional[Ticket]:
        result = await self._session.execute(select(TicketModel).where(TicketModel.uuid == ticket_uuid))
        model = result.scalars().one_or_none()
        if not model:
            return None
        model.is_archived = is_archived
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)


class CommentRepository(CommentRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_entity(self, m: CommentModel) -> Comment:
        return Comment(
            id=m.id,
            ticket_id=m.ticket_id,
            user_id=m.user_id,
            content=m.content,
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    async def create(self, ticket_id: int, user_id: int, content: str) -> Comment:
        model = CommentModel(ticket_id=ticket_id, user_id=user_id, content=content)
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def get_by_ticket(self, ticket_id: int) -> Sequence[Comment]:
        result = await self._session.execute(
            select(CommentModel).where(CommentModel.ticket_id == ticket_id).order_by(CommentModel.created_at.asc())
        )
        rows = result.scalars().all()
        return [self._to_entity(r) for r in rows]

    async def get_by_id(self, comment_id: int) -> Optional[Comment]:
        result = await self._session.execute(select(CommentModel).where(CommentModel.id == comment_id))
        row = result.scalars().one_or_none()
        return self._to_entity(row) if row else None

    async def update(self, comment_id: int, content: str) -> Optional[Comment]:
        result = await self._session.execute(select(CommentModel).where(CommentModel.id == comment_id))
        model = result.scalars().one_or_none()
        if not model:
            return None
        model.content = content
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def delete(self, comment_id: int) -> bool:
        result = await self._session.execute(select(CommentModel).where(CommentModel.id == comment_id))
        model = result.scalars().one_or_none()
        if not model:
            return False
        await self._session.delete(model)
        await self._session.flush()
        return True
