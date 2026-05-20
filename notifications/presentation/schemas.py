from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


class NotificationResponse(BaseModel):
    id: int
    uuid: str
    title: str
    description: str
    photo_path: Optional[str] = None
    recipient_user_id: Optional[int] = None
    notification_type: str = "general"
    is_archived: bool = False
    created_at: datetime
    updated_at: datetime


class NotificationCreate(BaseModel):
    title: str
    description: str
    photo_path: Optional[str] = None
    recipient_user_id: Optional[int] = None
    notification_type: str = "general"


class NotificationUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    photo_path: Optional[str] = None


class NotificationArchive(BaseModel):
    is_archived: bool = True
