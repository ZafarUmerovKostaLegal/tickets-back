from dataclasses import dataclass
from datetime import datetime


@dataclass
class HealthEntity:
    status: str
    service: str
    timestamp: datetime
