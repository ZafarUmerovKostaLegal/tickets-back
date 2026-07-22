import uuid
from typing import Optional, Sequence

from domain.entities import User
from domain.roles import Role
from application.ports import UserRepositoryPort, TokenServicePort, RoleRepositoryPort
from application.session_policy import session_jti_is_valid
from infrastructure.config import get_settings


class AzureLoginUseCase:
    def __init__(self, user_repo: UserRepositoryPort, token_service: TokenServicePort):
        self._user_repo = user_repo
        self._token_service = token_service

    async def execute(
        self,
        azure_oid: str,
        email: str,
        display_name: Optional[str],
        picture: Optional[str],
        role: str = Role.EMPLOYEE.value,
    ) -> tuple[User, str]:

        user = await self._user_repo.get_by_azure_oid(azure_oid)
        if not user:
            user = await self._user_repo.create(
                azure_oid, email, display_name, picture, role
            )
        else:
            dn = (display_name or "").strip() or None
            pic: Optional[str] = None
            if isinstance(picture, str):
                pic = picture.strip() or None
            if dn is not None or pic is not None:
                updated = await self._user_repo.update_profile(user.id, dn, pic, None)
                if updated is not None:
                    user = updated
        jti = str(uuid.uuid4())
        await self._user_repo.register_session_jti(
            user.id,
            jti,
            max_sessions=get_settings().auth_max_concurrent_sessions,
        )
        token = self._token_service.create_access_token(user.id, user.azure_oid, jti)
        return user, token


class GetCurrentUserUseCase:
    def __init__(self, user_repo: UserRepositoryPort, token_service: TokenServicePort):
        self._user_repo = user_repo
        self._token_service = token_service

    async def execute(self, access_token: str) -> Optional[User]:
        payload = self._token_service.decode_token(access_token)
        if not payload:
            return None
        user_id = payload.get("sub")
        if not user_id:
            return None
        user = await self._user_repo.get_by_id(int(user_id))
        if not user:
            return None
        active_jtis = await self._user_repo.list_active_session_jtis(user.id)
        if not self._session_matches(payload, user, active_jtis):
            return None
        return user

    def _session_matches(
        self,
        payload: dict,
        user: User,
        active_jtis: list[str],
    ) -> bool:
        token_jti = payload.get("jti")
        if isinstance(token_jti, str):
            token_jti = token_jti.strip() or None
        else:
            token_jti = None
        return session_jti_is_valid(
            token_jti,
            active_jtis,
            legacy_jti=user.active_session_jti,
        )


class InvalidateSessionUseCase:


    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int, jti: Optional[str] = None) -> None:
        if jti:
            await self._user_repo.remove_session_jti(user_id, jti)
        else:
            await self._user_repo.clear_all_session_jtis(user_id)


class UpdateProfileUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(
        self,
        user_id: int,
        display_name: Optional[str],
        picture: Optional[str],
        role: Optional[str],
    ) -> Optional[User]:
        return await self._user_repo.update_profile(
            user_id, display_name, picture, role
        )


class ListUsersUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(
        self,
        include_archived: bool = False,
    ) -> list:
        return list(await self._user_repo.get_all(include_archived=include_archived))

    async def execute_page(
        self,
        *,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 50,
        q: str | None = None,
        role: str | None = None,
    ) -> tuple[list, int, dict]:
        items, total = await self._user_repo.list_page(
            include_archived=include_archived,
            skip=skip,
            limit=limit,
            q=q,
            role=role,
            exclude_hidden_system=True,
        )
        summary = await self._user_repo.list_summary(
            include_archived=include_archived,
            exclude_hidden_system=True,
        )
        return list(items), total, summary


class SetRoleUseCase:
    def __init__(self, user_repo: UserRepositoryPort, role_repo: RoleRepositoryPort):
        self._user_repo = user_repo
        self._role_repo = role_repo

    async def execute(self, user_id: int, role: str) -> Optional[User]:
        r = await self._role_repo.get_by_name(role.strip())
        if not r:
            return None
        return await self._user_repo.set_role(user_id, r["name"])


class BlockUserUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int, is_blocked: bool) -> Optional[User]:
        return await self._user_repo.set_blocked(user_id, is_blocked)


class ArchiveUserUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int, is_archived: bool) -> Optional[User]:
        return await self._user_repo.set_archived(user_id, is_archived)


class SetTimeTrackingRoleUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int, time_tracking_role: Optional[str]) -> Optional[User]:
        return await self._user_repo.set_time_tracking_role(user_id, time_tracking_role)


class SetPositionUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int, position: Optional[str]) -> Optional[User]:
        return await self._user_repo.set_position(user_id, position)


class SetDesktopBackgroundUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int, path: Optional[str]) -> Optional[User]:
        return await self._user_repo.set_desktop_background(user_id, path)


class SetInitialsUseCase:
    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int, initials: Optional[str]) -> Optional[User]:
        return await self._user_repo.set_initials(user_id, initials)


class ListRolesUseCase:
    def __init__(self, role_repo: RoleRepositoryPort):
        self._role_repo = role_repo

    async def execute(self) -> Sequence[dict]:
        return await self._role_repo.list_all()


class CreateRoleUseCase:
    def __init__(self, role_repo: RoleRepositoryPort):
        self._role_repo = role_repo

    async def execute(self, name: str) -> Optional[dict]:
        name = (name or "").strip()
        if not name:
            return None
        existing = await self._role_repo.get_by_name(name)
        if existing:
            return None
        return await self._role_repo.create(name)


class UpdateRoleUseCase:
    def __init__(self, role_repo: RoleRepositoryPort):
        self._role_repo = role_repo

    async def execute(self, role_id: int, name: str) -> Optional[dict]:
        name = (name or "").strip()
        if not name:
            return None
        existing = await self._role_repo.get_by_name(name)
        if existing and existing["id"] != role_id:
            return None
        return await self._role_repo.update(role_id, name)


class DeleteRoleUseCase:
    def __init__(self, role_repo: RoleRepositoryPort):
        self._role_repo = role_repo

    async def execute(self, role_id: int) -> tuple[bool, str]:
        role = await self._role_repo.get_by_id(role_id)
        if not role:
            return False, "not_found"
        n = await self._role_repo.count_users_with_role(role["name"])
        if n > 0:
            return False, "role_in_use"
        ok = await self._role_repo.delete(role_id)
        return ok, "ok"


class GetRolePermissionsUseCase:
    def __init__(self, role_repo: RoleRepositoryPort):
        self._role_repo = role_repo

    async def execute(self, role_id: int) -> Optional[dict]:
        role = await self._role_repo.get_by_id(role_id)
        if not role:
            return None
        return await self._role_repo.get_permissions(role_id)


class SetRolePermissionsUseCase:
    def __init__(self, role_repo: RoleRepositoryPort):
        self._role_repo = role_repo

    async def execute(self, role_id: int, permissions: dict) -> bool:
        role = await self._role_repo.get_by_id(role_id)
        if not role:
            return False
        await self._role_repo.set_permissions(role_id, permissions)
        return True
