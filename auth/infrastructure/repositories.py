from typing import Optional, Sequence
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from domain.entities import User
from application.ports import UserRepositoryPort, RoleRepositoryPort
from infrastructure.models import UserModel, RoleModel, RolePermissionModel, LocalAdminCredentialModel


class RoleRepository(RoleRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def list_all(self) -> Sequence[dict]:
        result = await self._session.execute(select(RoleModel).order_by(RoleModel.name))
        rows = result.scalars().all()
        return [{"id": r.id, "name": r.name} for r in rows]

    async def get_by_id(self, role_id: int) -> Optional[dict]:
        result = await self._session.execute(select(RoleModel).where(RoleModel.id == role_id))
        row = result.scalars().one_or_none()
        return {"id": row.id, "name": row.name} if row else None

    async def get_by_name(self, name: str) -> Optional[dict]:
        result = await self._session.execute(select(RoleModel).where(RoleModel.name == name))
        row = result.scalars().one_or_none()
        return {"id": row.id, "name": row.name} if row else None

    async def create(self, name: str) -> dict:
        model = RoleModel(name=name.strip())
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return {"id": model.id, "name": model.name}

    async def update(self, role_id: int, name: str) -> Optional[dict]:
        await self._session.execute(
            update(RoleModel).where(RoleModel.id == role_id).values(name=name.strip())
        )
        await self._session.flush()
        return await self.get_by_id(role_id)

    async def delete(self, role_id: int) -> bool:
        result = await self._session.execute(delete(RoleModel).where(RoleModel.id == role_id))
        await self._session.flush()
        return result.rowcount > 0

    async def count_users_with_role(self, role_name: str) -> int:
        result = await self._session.execute(
            select(func.count(UserModel.id)).where(UserModel.role == role_name)
        )
        return result.scalar() or 0

    async def get_permissions(self, role_id: int) -> dict:
        result = await self._session.execute(
            select(RolePermissionModel).where(RolePermissionModel.role_id == role_id)
        )
        rows = result.scalars().all()
        return {r.permission_key: r.allowed for r in rows}

    async def set_permissions(self, role_id: int, permissions: dict) -> None:
        await self._session.execute(delete(RolePermissionModel).where(RolePermissionModel.role_id == role_id))
        for key, allowed in permissions.items():
            if not key or not key.strip():
                continue
            self._session.add(
                RolePermissionModel(role_id=role_id, permission_key=key.strip(), allowed=bool(allowed))
            )
        await self._session.flush()


class UserRepository(UserRepositoryPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _to_entity(self, m: UserModel) -> User:
        return User(
            id=m.id,
            azure_oid=m.azure_oid,
            email=m.email,
            display_name=m.display_name,
            picture=m.picture,
            role=m.role,
            position=getattr(m, "position", None),
            is_blocked=m.is_blocked,
            is_archived=m.is_archived,
            time_tracking_role=getattr(m, "time_tracking_role", None),
            desktop_background=getattr(m, "desktop_background", None),
            active_session_jti=getattr(m, "active_session_jti", None),
            created_at=m.created_at,
            updated_at=m.updated_at,
        )

    async def get_by_azure_oid(self, azure_oid: str) -> Optional[User]:
        result = await self._session.execute(select(UserModel).where(UserModel.azure_oid == azure_oid))
        row = result.scalars().one_or_none()
        return self._to_entity(row) if row else None

    async def get_by_id(self, user_id: int) -> Optional[User]:
        result = await self._session.execute(select(UserModel).where(UserModel.id == user_id))
        row = result.scalars().one_or_none()
        return self._to_entity(row) if row else None

    async def get_all(
        self,
        include_archived: bool = False,
    ) -> Sequence[User]:
        q = select(UserModel).order_by(UserModel.id)
        if not include_archived:
            q = q.where(UserModel.is_archived == False)
        result = await self._session.execute(q)
        rows = result.scalars().all()
        return [self._to_entity(r) for r in rows]

    async def create(
        self,
        azure_oid: str,
        email: str,
        display_name: Optional[str],
        picture: Optional[str],
        role: str,
    ) -> User:
        model = UserModel(
            azure_oid=azure_oid,
            email=email,
            display_name=display_name,
            picture=picture,
            role=role,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return self._to_entity(model)

    async def update_profile(
        self,
        user_id: int,
        display_name: Optional[str],
        picture: Optional[str],
        role: Optional[str],
    ) -> Optional[User]:
        values = {}
        if display_name is not None:
            values["display_name"] = display_name
        if picture is not None:
            values["picture"] = picture
        if role is not None:
            values["role"] = role
        if values:
            await self._session.execute(
                update(UserModel).where(UserModel.id == user_id).values(**values)
            )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_role(self, user_id: int, role: str) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(role=role)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_blocked(self, user_id: int, is_blocked: bool) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(is_blocked=is_blocked)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_archived(self, user_id: int, is_archived: bool) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(is_archived=is_archived)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_time_tracking_role(self, user_id: int, time_tracking_role: Optional[str]) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(time_tracking_role=time_tracking_role)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_position(self, user_id: int, position: Optional[str]) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(position=position)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_desktop_background(self, user_id: int, path: Optional[str]) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(desktop_background=path)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_active_session_jti(self, user_id: int, jti: Optional[str]) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(active_session_jti=jti)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def get_local_admin_credentials(self) -> Optional[tuple[str, str]]:

        result = await self._session.execute(
            select(LocalAdminCredentialModel).where(LocalAdminCredentialModel.id == 1)
        )
        row = result.scalars().one_or_none()
        if not row:
            return None
        return (row.username, row.password_hash)

    async def save_local_admin_credentials(self, username: str, password_hash: str) -> None:
        result = await self._session.execute(
            select(LocalAdminCredentialModel).where(LocalAdminCredentialModel.id == 1)
        )
        existing = result.scalars().one_or_none()
        if existing:
            existing.username = username.strip()
            existing.password_hash = password_hash
        else:
            self._session.add(
                LocalAdminCredentialModel(
                    id=1,
                    username=username.strip(),
                    password_hash=password_hash,
                )
            )
        await self._session.flush()
