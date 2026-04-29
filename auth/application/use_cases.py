import secrets
import uuid
from typing import Optional, Sequence

import bcrypt

from domain.entities import User
from domain.roles import Role
from application.ports import UserRepositoryPort, TokenServicePort, RoleRepositoryPort


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
        await self._user_repo.set_active_session_jti(user.id, jti)
        token = self._token_service.create_access_token(user.id, user.azure_oid, jti)
        return user, token


LOCAL_ADMIN_OID = "local-admin"


class AdminLoginUseCase:
    def __init__(
        self,
        user_repo: UserRepositoryPort,
        token_service: TokenServicePort,
        admin_username: str,
        admin_password: str,
    ):
        self._user_repo = user_repo
        self._token_service = token_service
        self._admin_username = admin_username
        self._admin_password = admin_password

    async def execute(self, username: str, password: str) -> str | None:
        creds = await self._user_repo.get_local_admin_credentials()
        if creds:
            stored_user, stored_hash = creds
            if (username or "").strip() != (stored_user or "").strip():
                return None
            try:
                if not bcrypt.checkpw(
                    (password or "").encode("utf-8"),
                    stored_hash.encode("ascii"),
                ):
                    return None
            except (ValueError, TypeError):
                return None
        else:
            if not (self._admin_password or "").strip():
                return None
            if (username or "").strip() != (self._admin_username or "").strip() or (password or "") != self._admin_password:
                return None
        user = await self._user_repo.get_by_azure_oid(LOCAL_ADMIN_OID)
        if not user:
            user = await self._user_repo.create(
                azure_oid=LOCAL_ADMIN_OID,
                email="admin@local",
                display_name="Главный администратор",
                picture=None,
                role=Role.MAIN_ADMIN.value,
            )
        jti = str(uuid.uuid4())
        await self._user_repo.set_active_session_jti(user.id, jti)
        return self._token_service.create_access_token(user.id, user.azure_oid, jti)


class BootstrapAdminUseCase:


    def __init__(
        self,
        user_repo: UserRepositoryPort,
        admin_username: str,
        bootstrap_secret: str,
    ):
        self._user_repo = user_repo
        self._admin_username = (admin_username or "admin").strip()
        self._bootstrap_secret = (bootstrap_secret or "").strip()

    async def execute(self, secret: str) -> tuple[str, str] | None:

        if not self._bootstrap_secret:
            return None
        if (secret or "").strip() != self._bootstrap_secret:
            return None
        if await self._user_repo.get_local_admin_credentials():
            return None
        plain = secrets.token_urlsafe(18)
        pw_hash = bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")
        await self._user_repo.save_local_admin_credentials(self._admin_username, pw_hash)
        user = await self._user_repo.get_by_azure_oid(LOCAL_ADMIN_OID)
        if not user:
            await self._user_repo.create(
                azure_oid=LOCAL_ADMIN_OID,
                email="admin@local",
                display_name="Главный администратор",
                picture=None,
                role=Role.MAIN_ADMIN.value,
            )
        else:
            await self._user_repo.set_role(user.id, Role.MAIN_ADMIN.value)
        return (self._admin_username, plain)


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
        return self._session_matches(payload, user)

    def _session_matches(self, payload: dict, user: User) -> Optional[User]:
        token_jti = payload.get("jti")
        stored = user.active_session_jti
        if stored:
            if not token_jti or token_jti != stored:
                return None
        else:

            if token_jti:
                return None
        return user


class InvalidateSessionUseCase:


    def __init__(self, user_repo: UserRepositoryPort):
        self._user_repo = user_repo

    async def execute(self, user_id: int) -> None:
        await self._user_repo.set_active_session_jti(user_id, secrets.token_urlsafe(48))


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
