from abc import ABC, abstractmethod


class HealthRepositoryPort(ABC):
    @abstractmethod
    async def check(self) -> bool:
        pass
