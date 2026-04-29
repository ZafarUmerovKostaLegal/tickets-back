from application.ports import HealthRepositoryPort


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
