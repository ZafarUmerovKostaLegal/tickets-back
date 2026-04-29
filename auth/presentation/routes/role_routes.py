
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.use_cases import (
    ListRolesUseCase,
    CreateRoleUseCase,
    UpdateRoleUseCase,
    DeleteRoleUseCase,
    GetRolePermissionsUseCase,
    SetRolePermissionsUseCase,
)
from application.ports import RoleRepositoryPort
from domain.roles import Role
from infrastructure.database import get_session
from infrastructure.repositories import UserRepository, RoleRepository
from presentation.schemas import (
    RoleResponse,
    RoleCreateRequest,
    RoleUpdateRequest,
    RolePermissionsResponse,
    RolePermissionsUpdateRequest,
)
from presentation.routes.user_routes import get_current_user
from domain.entities import User

router = APIRouter(prefix="/roles", tags=["roles"])


def get_role_repo(session: AsyncSession = Depends(get_session)) -> RoleRepositoryPort:
    return RoleRepository(session)


def require_main_admin(current_user: User = Depends(get_current_user)) -> User:

    if (current_user.role or "").strip() != Role.MAIN_ADMIN.value:
        raise HTTPException(status_code=403, detail="Only Main Administrator can manage roles")
    return current_user


@router.get("", response_model=list[RoleResponse])
async def list_roles(
    session: AsyncSession = Depends(get_session),
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
    current_user: User = Depends(get_current_user),
):

    uc = ListRolesUseCase(role_repo)
    roles = await uc.execute()
    return [RoleResponse(id=r["id"], name=r["name"]) for r in roles]


@router.post("", response_model=RoleResponse, status_code=201)
async def create_role(
    body: RoleCreateRequest,
    session: AsyncSession = Depends(get_session),
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
    current_user: User = Depends(require_main_admin),
):

    uc = CreateRoleUseCase(role_repo)
    role = await uc.execute(body.name)
    await session.commit()
    if not role:
        raise HTTPException(
            status_code=400,
            detail="Role name is empty or already exists",
        )
    return RoleResponse(id=role["id"], name=role["name"])


@router.get("/{role_id}", response_model=RoleResponse)
async def get_role(
    role_id: int,
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
    current_user: User = Depends(get_current_user),
):

    role = await role_repo.get_by_id(role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Role not found")
    return RoleResponse(id=role["id"], name=role["name"])


@router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: int,
    body: RoleUpdateRequest,
    session: AsyncSession = Depends(get_session),
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
    current_user: User = Depends(require_main_admin),
):

    uc = UpdateRoleUseCase(role_repo)
    role = await uc.execute(role_id, body.name)
    await session.commit()
    if not role:
        raise HTTPException(
            status_code=400,
            detail="Role not found or name already exists",
        )
    return RoleResponse(id=role["id"], name=role["name"])


@router.delete("/{role_id}", status_code=204)
async def delete_role(
    role_id: int,
    session: AsyncSession = Depends(get_session),
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
    current_user: User = Depends(require_main_admin),
):

    uc = DeleteRoleUseCase(role_repo)
    ok, reason = await uc.execute(role_id)
    await session.commit()
    if not ok:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="Role not found")
        raise HTTPException(
            status_code=400,
            detail="Cannot delete role: it is assigned to one or more users",
        )


@router.get("/{role_id}/permissions", response_model=RolePermissionsResponse)
async def get_role_permissions(
    role_id: int,
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
    current_user: User = Depends(get_current_user),
):

    uc = GetRolePermissionsUseCase(role_repo)
    perms = await uc.execute(role_id)
    if perms is None:
        raise HTTPException(status_code=404, detail="Role not found")
    return RolePermissionsResponse(permissions=perms)


@router.patch("/{role_id}/permissions", response_model=RolePermissionsResponse)
async def set_role_permissions(
    role_id: int,
    body: RolePermissionsUpdateRequest,
    session: AsyncSession = Depends(get_session),
    role_repo: RoleRepositoryPort = Depends(get_role_repo),
    current_user: User = Depends(require_main_admin),
):

    uc = SetRolePermissionsUseCase(role_repo)
    ok = await uc.execute(role_id, body.permissions or {})
    await session.commit()
    if not ok:
        raise HTTPException(status_code=404, detail="Role not found")
    perms = await role_repo.get_permissions(role_id)
    return RolePermissionsResponse(permissions=perms)
