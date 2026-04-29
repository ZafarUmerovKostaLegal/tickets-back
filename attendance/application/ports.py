from abc import ABC, abstractmethod
from datetime import time
from typing import Optional

from domain.entities import WorkdaySettings


class HealthRepositoryPort(ABC):
    @abstractmethod
    async def check(self) -> bool:
        pass


class WorkdaySettingsRepositoryPort(ABC):
    @abstractmethod
    async def get(self) -> Optional[WorkdaySettings]:

        pass

    @abstractmethod
    async def save(
        self,
        workday_start: time,
        workday_end: time,
        late_threshold_minutes: int,
        daily_hours_norm: int,
    ) -> WorkdaySettings:

        pass
