from typing import Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from application.use_cases import (
    GetCurrentUserUseCase,
    ListUsersUseCase,
    SetRoleUseCase,
    BlockUserUseCase,
    ArchiveUserUseCase,
    SetTimeTrackingRoleUseCase,
    SetPositionUseCase,
    SetDesktopBackgroundUseCase,
)
from application.ports import UserRepositoryPort, TokenServicePort, RoleRepositoryPort
from backend_common.rbac_ui_permissions import build_ui_permissions
from domain.entities import User
from infrastructure.database import get_session
from infrastructure.repositories import UserRepository, RoleRepository
from infrastructure.jwt_service import JWTService
from domain.roles import Role
from presentation.http_auth import access_token_from_request
from presentation.schemas import (
    UserResponse,
    UserDetailResponse,
    SetRoleRequest,
    BlockUserRequest,
    ArchiveUserRequest,
    TimeTrackingRoleRequest,
    SetPositionRequest,
    SetDesktopBackgroundRequest,
)

router = APIRouter(prefix="/users", tags=["users"])


def get_user_repo(session: AsyncSession = Depends(get_session)) -> UserRepositoryPort:
    return UserRepository(session)


def get_role_repo(session: AsyncSession = Depends(get_session)) -> RoleRepositoryPort:
    return RoleRepository(session)


def get_token_service() -> TokenServicePort:
    return JWTService()


async def get_current_user(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    token_service: TokenServicePort = Depends(get_token_service),
) -> User:
    token = access_token_from_request(request, authorization)
    uc = GetCurrentUserUseCase(user_repo, token_service)
    user = await uc.execute(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


def _user_to_response(user: User, *, omit_permissions: bool = False) -> UserResponse:
    perms = None if omit_permissions else build_ui_permissions(user.role, user.time_tracking_role)
    return UserResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        picture=user.picture,
        role=user.role,
        position=user.position,
        is_blocked=user.is_blocked,
        is_archived=user.is_archived,
        created_at=user.created_at,
        updated_at=user.updated_at,
        permissions=perms,
        time_tracking_role=user.time_tracking_role,
        desktop_background=user.desktop_background,
    )


def _user_to_detail(user: User) -> UserDetailResponse:
    return UserDetailResponse(
        id=user.id,
        azure_oid=user.azure_oid,
        email=user.email,
        display_name=user.display_name,
        picture=user.picture,
        role=user.role,
        position=user.position,
        is_blocked=user.is_blocked,
        is_archived=user.is_archived,
        time_tracking_role=user.time_tracking_role,
        desktop_background=user.desktop_background,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )


def require_main_admin(current_user: User = Depends(get_current_user)) -> User:

    if (current_user.role or "").strip() != Role.MAIN_ADMIN.value:
        raise HTTPException(status_code=403, detail="Only Main Administrator can perform this action")
    return current_user


def require_assign_user_role(current_user: User = Depends(get_current_user)) -> User:

    role = (current_user.role or "").strip()
    if role not in (Role.MAIN_ADMIN.value, Role.ADMIN.value):
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator or Administrator can assign user roles",
        )
    return current_user


def require_main_admin_or_admin(current_user: User = Depends(get_current_user)) -> User:

    role = (current_user.role or "").strip()
    if role not in (Role.MAIN_ADMIN.value, Role.ADMIN.value):
        raise HTTPException(status_code=403, detail="Only Main Administrator or Administrator can manage time tracking access")
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:

    role = (current_user.role or "").strip()
    if role not in (Role.MAIN_ADMIN.value, Role.ADMIN.value, Role.PARTNER.value):
        raise HTTPException(status_code=403, detail="Only Main Administrator, Administrator or Partner can block or archive users")
    return current_user


ROLES_CAN_VIEW_USER_DIRECTORY = {
    Role.MAIN_ADMIN.value,
    Role.ADMIN.value,
    Role.PARTNER.value,
    Role.IT_DEPARTMENT.value,
    Role.OFFICE_MANAGER.value,

    "Офис-менеджер",
}


def require_view_user_directory(current_user: User = Depends(get_current_user)) -> User:

    role = (current_user.role or "").strip()
    if role not in ROLES_CAN_VIEW_USER_DIRECTORY:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator, Administrator, Partner, IT department or Office manager can list users",
        )
    return current_user


def require_user_detail_access(
    user_id: int,
    current_user: User = Depends(get_current_user),
) -> User:

    role = (current_user.role or "").strip()
    if current_user.id == user_id:
        return current_user
    if role not in ROLES_CAN_VIEW_USER_DIRECTORY:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator, Administrator, Partner, IT department or Office manager can view this profile",
        )
    return current_user


@router.get("", response_model=list[UserResponse])
async def list_users(
    include_archived: bool = Query(False, description="Include archived users"),
    current_user: User = Depends(require_view_user_directory),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):
    uc = ListUsersUseCase(user_repo)
    users = await uc.execute(include_archived=include_archived)
    return [_user_to_response(u, omit_permissions=True) for u in users]


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return _user_to_response(current_user)


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user_detail(
    user_id: int,
    current_user: User = Depends(require_user_detail_access),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_detail(user)


@router.patch("/{user_id}/role", response_model=UserDetailResponse)
async def set_user_role(
    user_id: int,
    body: SetRoleRequest,
    current_user: User = Depends(require_assign_user_role),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
):

    role_to_assign = (body.role or "").strip()
    if role_to_assign == Role.MAIN_ADMIN.value and (current_user.role or "").strip() != Role.MAIN_ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail="Only Main Administrator can assign the Main Administrator role",
        )
    uc = SetRoleUseCase(user_repo, role_repo)
    user = await uc.execute(user_id, body.role)
    await session.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found or role does not exist")
    return _user_to_detail(user)


@router.patch("/{user_id}/block", response_model=UserDetailResponse)
async def block_user(
    user_id: int,
    body: BlockUserRequest,
    current_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):
    uc = BlockUserUseCase(user_repo)
    user = await uc.execute(user_id, body.is_blocked)
    await session.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_detail(user)


@router.patch("/{user_id}/archive", response_model=UserDetailResponse)
async def archive_user(
    user_id: int,
    body: ArchiveUserRequest,
    current_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):
    uc = ArchiveUserUseCase(user_repo)
    user = await uc.execute(user_id, body.is_archived)
    await session.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_detail(user)


@router.patch("/{user_id}/time-tracking-role", response_model=UserDetailResponse)
async def set_time_tracking_role(
    user_id: int,
    body: TimeTrackingRoleRequest,
    current_user: User = Depends(require_main_admin_or_admin),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):

    row = await user_repo.get_by_id(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    value = (body.time_tracking_role or "").strip() or None
    if value is not None and value not in ("user", "manager"):
        raise HTTPException(status_code=400, detail="time_tracking_role must be 'user', 'manager' or null")

    if value in ("user", "manager"):
        if body.position is not None:
            pos_val = (body.position or "").strip() or None
            pos_uc = SetPositionUseCase(user_repo)
            row = await pos_uc.execute(user_id, pos_val)
            if not row:
                raise HTTPException(status_code=404, detail="User not found")
        row = await user_repo.get_by_id(user_id)
        if not row or not (row.position or "").strip():
            raise HTTPException(
                status_code=400,
                detail="Для роли в учёте времени (user или manager) нужна непустая должность: укажите position в теле запроса либо задайте должность в профиле.",
            )

    uc = SetTimeTrackingRoleUseCase(user_repo)
    user = await uc.execute(user_id, value)
    await session.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_detail(user)


@router.patch("/{user_id}/position", response_model=UserDetailResponse)
async def set_position(
    user_id: int,
    body: SetPositionRequest,
    current_user: User = Depends(require_main_admin_or_admin),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):

    row = await user_repo.get_by_id(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    value = (body.position or "").strip() or None
    tt = (row.time_tracking_role or "").strip()
    if value is None and tt in ("user", "manager"):
        raise HTTPException(
            status_code=400,
            detail="Нельзя очистить должность, пока у пользователя роль в учёте времени (user или manager).",
        )
    uc = SetPositionUseCase(user_repo)
    user = await uc.execute(user_id, value)
    await session.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_detail(user)


@router.patch("/me/desktop-background", response_model=UserResponse)
async def set_my_desktop_background(
    body: SetDesktopBackgroundRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):

    path = (body.path or "").strip() or None
    uc = SetDesktopBackgroundUseCase(user_repo)
    user = await uc.execute(current_user.id, path)
    await session.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_response(user)


@router.delete("/me/desktop-background", response_model=UserResponse)
async def delete_my_desktop_background(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    user_repo: UserRepositoryPort = Depends(get_user_repo),
):

    uc = SetDesktopBackgroundUseCase(user_repo)
    user = await uc.execute(current_user.id, None)
    await session.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return _user_to_response(user)
