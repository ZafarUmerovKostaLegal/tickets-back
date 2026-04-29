from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class HealthEntity:
    status: str
    service: str
    timestamp: datetime
