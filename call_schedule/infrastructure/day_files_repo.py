from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import CallScheduleDayFileModel


class DayFilesRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_for_day(self, day: date) -> list[CallScheduleDayFileModel]:
        q = (
            select(CallScheduleDayFileModel)
            .where(CallScheduleDayFileModel.day == day)
            .order_by(CallScheduleDayFileModel.uploaded_at.desc())
        )
        return list((await self._session.execute(q)).scalars().all())

    async def get_by_id(self, file_id: str) -> CallScheduleDayFileModel | None:
        return await self._session.get(CallScheduleDayFileModel, file_id)

    async def add(
        self,
        *,
        file_id: str,
        day: date,
        original_name: str,
        content_type: str | None,
        size_bytes: int,
        storage_key: str,
        uploaded_by_user_id: int,
    ) -> CallScheduleDayFileModel:
        row = CallScheduleDayFileModel(
            id=file_id,
            day=day,
            original_name=original_name,
            content_type=content_type,
            size_bytes=size_bytes,
            storage_key=storage_key,
            uploaded_by_user_id=uploaded_by_user_id,
            uploaded_at=datetime.now(timezone.utc),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def delete(self, row: CallScheduleDayFileModel) -> None:
        await self._session.delete(row)
        await self._session.flush()

    async def counts_in_range(self, date_from: date, date_to: date) -> dict[str, int]:
        q = (
            select(CallScheduleDayFileModel.day, func.count())
            .where(
                CallScheduleDayFileModel.day >= date_from,
                CallScheduleDayFileModel.day <= date_to,
            )
            .group_by(CallScheduleDayFileModel.day)
        )
        rows = (await self._session.execute(q)).all()
        return {d.isoformat(): int(n) for d, n in rows}
