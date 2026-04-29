from datetime import time
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from application.ports import HealthRepositoryPort, WorkdaySettingsRepositoryPort
from domain.entities import WorkdaySettings
from infrastructure.models import WorkdaySettingsModel


class HealthRepository(HealthRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def check(self) -> bool:
        try:
            await self._session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class WorkdaySettingsRepository(WorkdaySettingsRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_entity(self, m: WorkdaySettingsModel) -> WorkdaySettings:
        return WorkdaySettings(
            workday_start=m.workday_start,
            workday_end=m.workday_end,
            late_threshold_minutes=m.late_threshold_minutes,
            daily_hours_norm=m.daily_hours_norm,
        )

    async def get(self) -> WorkdaySettings | None:
        result = await self._session.execute(select(WorkdaySettingsModel).limit(1))
        row = result.scalars().one_or_none()
        return self._to_entity(row) if row else None

    async def save(
        self,
        workday_start: time,
        workday_end: time,
        late_threshold_minutes: int,
        daily_hours_norm: int,
    ) -> WorkdaySettings:
        result = await self._session.execute(select(WorkdaySettingsModel).limit(1))
        model = result.scalars().one_or_none()
        if model is None:
            model = WorkdaySettingsModel(
                workday_start=workday_start,
                workday_end=workday_end,
                late_threshold_minutes=late_threshold_minutes,
                daily_hours_norm=daily_hours_norm,
            )
            self._session.add(model)
        else:
            model.workday_start = workday_start
            model.workday_end = workday_end
            model.late_threshold_minutes = late_threshold_minutes
            model.daily_hours_norm = daily_hours_norm
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)
