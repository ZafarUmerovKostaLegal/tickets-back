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


class ProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    picture: Optional[str] = None
    role: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminBootstrapRequest(BaseModel):


    secret: str


class AdminBootstrapResponse(BaseModel):
    username: str
    password: str
    message: str = (
        "Сохраните пароль в надёжном месте. Повторный запрос вернёт ошибку. "
        "Секрет ADMIN_BOOTSTRAP_SECRET после этого можно убрать из окружения."
    )


class AdminBootstrapStatusResponse(BaseModel):


    bootstrap_available: bool
    credentials_in_database: bool


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
