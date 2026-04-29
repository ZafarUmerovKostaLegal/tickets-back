from datetime import datetime, time
from domain.entities import HealthEntity, WorkdaySettings
from application.ports import HealthRepositoryPort, WorkdaySettingsRepositoryPort
from infrastructure.config import get_settings


class GetHealthUseCase:
    def __init__(self, health_repo: HealthRepositoryPort):
        self._health_repo = health_repo

    async def execute(self, service_name: str) -> HealthEntity:
        db_ok = await self._health_repo.check()
        status = "healthy" if db_ok else "degraded"
        return HealthEntity(
            status=status,
            service=service_name,
            timestamp=datetime.utcnow(),
        )


class GetWorkdaySettingsUseCase:
    def __init__(self, settings_repo: WorkdaySettingsRepositoryPort):
        self._settings_repo = settings_repo

    async def execute(self) -> WorkdaySettings:
        settings = await self._settings_repo.get()
        if settings:
            return settings
        cfg = get_settings()
        default_start = time(hour=9, minute=0)
        default_end = time(hour=18, minute=0)
        default_late = 15
        default_norm = 8
        return await self._settings_repo.save(
            workday_start=default_start,
            workday_end=default_end,
            late_threshold_minutes=default_late,
            daily_hours_norm=default_norm,
        )


class UpdateWorkdaySettingsUseCase:
    def __init__(self, settings_repo: WorkdaySettingsRepositoryPort):
        self._settings_repo = settings_repo

    async def execute(
        self,
        workday_start: time | None,
        workday_end: time | None,
        late_threshold_minutes: int | None,
        daily_hours_norm: int | None,
    ) -> WorkdaySettings:
        current = await self._settings_repo.get()
        if not current:
            current = await GetWorkdaySettingsUseCase(self._settings_repo).execute()
        return await self._settings_repo.save(
            workday_start=workday_start or current.workday_start,
            workday_end=workday_end or current.workday_end,
            late_threshold_minutes=late_threshold_minutes
            if late_threshold_minutes is not None
            else current.late_threshold_minutes,
            daily_hours_norm=daily_hours_norm if daily_hours_norm is not None else current.daily_hours_norm,
        )
