from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from pydantic import BaseModel, Field


class UserResponse(BaseModel):
    id: int
    email: str
    display_name: Optional[str]
    picture: Optional[str]
    role: str
    position: Optional[str] = None
    is_blocked: bool = False
    is_archived: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    time_tracking_role: Optional[str] = None
    desktop_background: Optional[str] = None
    weekly_capacity_hours: Optional[float] = Field(
        None,
        description="Норма часов в неделю (учёт времени); null если пользователь не в БД time_tracking",
    )
    permissions: Optional[dict[str, Any]] = Field(
        None,
        description="Флаги разделов с auth (backend_common.rbac_ui_permissions); подсказка для UI, не замена проверкам API",
    )


class UserDetailResponse(BaseModel):
    id: int
    azure_oid: str
    email: str
    display_name: Optional[str]
    picture: Optional[str]
    role: str
    position: Optional[str] = None
    is_blocked: bool
    is_archived: bool
    time_tracking_role: Optional[str] = None
    desktop_background: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    weekly_capacity_hours: Optional[float] = Field(
        None,
        description="Норма часов в неделю (учёт времени); null если не в БД time_tracking",
    )


class SetRoleRequest(BaseModel):
    role: str


class BlockUserRequest(BaseModel):
    is_blocked: bool


class ArchiveUserRequest(BaseModel):
    is_archived: bool


class TimeTrackingRoleRequest(BaseModel):


    time_tracking_role: Optional[str] = None
    position: Optional[str] = Field(
        None,
        description="При назначении user/manager — непустая должность в теле или уже в профиле.",
    )


class SetPositionRequest(BaseModel):


    position: Optional[str] = None


class WeeklyCapacityPatchBody(BaseModel):


    weekly_capacity_hours: Decimal = Field(..., gt=0, le=168)
