from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import InternalExtensionModel


class DuplicateExtensionError(Exception):
    pass


class InternalExtensionRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self) -> list[InternalExtensionModel]:
        result = await self._session.execute(
            select(InternalExtensionModel).order_by(
                func.length(InternalExtensionModel.extension),
                InternalExtensionModel.extension,
                InternalExtensionModel.full_name,
            )
        )
        return list(result.scalars().all())

    async def get(self, extension_id: int) -> InternalExtensionModel | None:
        return await self._session.get(InternalExtensionModel, extension_id)

    async def create(self, *, full_name: str, extension: str) -> InternalExtensionModel:
        row = InternalExtensionModel(full_name=full_name, extension=extension)
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DuplicateExtensionError from exc
        return row

    async def update(
        self,
        row: InternalExtensionModel,
        *,
        full_name: str | None,
        extension: str | None,
    ) -> InternalExtensionModel:
        if full_name is not None:
            row.full_name = full_name
        if extension is not None:
            row.extension = extension
        row.updated_at = datetime.utcnow()
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise DuplicateExtensionError from exc
        return row

    async def delete(self, row: InternalExtensionModel) -> None:
        await self._session.delete(row)
        await self._session.flush()
