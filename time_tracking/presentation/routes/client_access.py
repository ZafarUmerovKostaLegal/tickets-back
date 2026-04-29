

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.repositories import ClientRepository
from infrastructure.models import TimeManagerClientModel


async def get_client_or_404(session: AsyncSession, client_id: str) -> TimeManagerClientModel:
    repo = ClientRepository(session)
    row = await repo.get_by_id(client_id)
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")
    return row


def ensure_client_not_archived(row: TimeManagerClientModel) -> None:
    if row.is_archived:
        raise HTTPException(
            status_code=400,
            detail="Клиент в архиве. Разархивируйте клиента (PATCH isArchived: false), чтобы добавлять или менять проекты, задачи и контакты.",
        )
