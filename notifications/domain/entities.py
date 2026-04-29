from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class Notification:
    id: int
    uuid: str
    title: str
    description: str
    photo_path: Optional[str]
    is_archived: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class HealthEntity:
    status: str
    service: str
    timestamp: datetime
