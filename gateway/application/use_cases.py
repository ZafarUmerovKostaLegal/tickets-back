from datetime import datetime
from application.ports import HealthRepositoryPort, MediaStoragePort
from domain.entities import HealthEntity


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


class SaveMediaUseCase:
    def __init__(self, media_storage: MediaStoragePort):
        self._media_storage = media_storage

    async def execute(self, filename: str, content: bytes) -> str:
        return await self._media_storage.save(filename, content)
