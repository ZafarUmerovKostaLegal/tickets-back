from abc import ABC, abstractmethod
from domain.entities import HealthEntity


class HealthRepositoryPort(ABC):
    @abstractmethod
    async def check(self) -> bool:
        pass


class MediaStoragePort(ABC):
    @abstractmethod
    async def save(self, filename: str, content: bytes) -> str:
        pass

    @abstractmethod
    async def get_path(self, filename: str) -> str:
        pass
