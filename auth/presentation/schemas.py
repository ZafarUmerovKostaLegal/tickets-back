from datetime import datetime
from typing import Optional

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
    permissions: Optional[dict] = None
    time_tracking_role: Optional[str] = None
    desktop_background: Optional[str] = None
    initials: Optional[str] = Field(None, max_length=8, description="Инициалы (3–8 букв)")


class UserPublicResponse(BaseModel):
    id: int
    email: str
    display_name: Optional[str] = None
    picture: Optional[str] = None
    role: Optional[str] = None
    position: Optional[str] = None
    initials: Optional[str] = Field(None, max_length=8, description="Инициалы (3–8 букв)")
    is_archived: bool = False


class UserPublicListResponse(BaseModel):
    items: list[UserPublicResponse]
    missing_ids: list[int] = []


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
    initials: Optional[str] = Field(None, max_length=8, description="Инициалы (3–8 букв)")
    created_at: datetime
    updated_at: datetime


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


class SetDesktopBackgroundRequest(BaseModel):


    path: str


class SetInitialsRequest(BaseModel):
    initials: Optional[str] = Field(
        None,
        min_length=3,
        max_length=8,
        description="От 3 до 8 букв (латиница или кириллица) или null для очистки",
    )


class ProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    picture: Optional[str] = None
    role: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


class RoleItem(BaseModel):
    value: str
    label: str


class RoleResponse(BaseModel):
    id: int
    name: str


class RoleCreateRequest(BaseModel):
    name: str


class RoleUpdateRequest(BaseModel):
    name: str


class RolePermissionsResponse(BaseModel):
    permissions: dict


class RolePermissionsUpdateRequest(BaseModel):

    permissions: Optional[dict] = None
