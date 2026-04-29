from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class Status(str, Enum):
    OPEN = "Открыт"
    CLOSED = "Закрыт"
    IN_PROGRESS = "В работе"
    ON_APPROVAL = "На согласовании"
    IMPOSSIBLE = "Невозможно выполнить"


class Priority(str, Enum):
    LOW = "Низкий"
    MEDIUM = "Средний"
    HIGH = "Высокий"
    CRITICAL = "Критический"


@dataclass
class Ticket:
    id: int
    uuid: str
    theme: str
    description: str
    attachment_path: Optional[str]
    status: str
    created_by_user_id: int
    created_at: datetime
    category: str
    priority: str
    is_archived: bool


@dataclass
class Comment:
    id: int
    ticket_id: int
    user_id: int
    content: str
    created_at: datetime
    updated_at: datetime


@dataclass
class HealthEntity:
    status: str
    service: str
    timestamp: datetime
