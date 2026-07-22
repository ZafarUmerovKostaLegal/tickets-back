from typing import Optional, Sequence, Tuple
from sqlalchemy import Integer, and_, func, or_, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from domain.entities import User
from application.ports import UserRepositoryPort, RoleRepositoryPort
from infrastructure.models import UserModel, RoleModel, RolePermissionModel, UserAuthSessionModel

_HIDDEN_LOCAL_PARTS = ("admin", "info")
_HIDDEN_EMAILS = ("admin@local",)
_HIDDEN_DISPLAY_NAMES = ("главный администратор",)


def _hidden_system_clause():
    email_l = func.lower(UserModel.email)
    local = func.split_part(email_l, "@", 1)
    display_l = func.lower(func.coalesce(UserModel.display_name, ""))
    return and_(
        email_l.notin_(_HIDDEN_EMAILS),
        local.notin_(_HIDDEN_LOCAL_PARTS),
        display_l.notin_(_HIDDEN_DISPLAY_NAMES),
    )


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
            initials=getattr(m, "initials", None),
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

    async def get_many_by_ids(self, user_ids: Sequence[int]) -> Sequence[User]:
        ids = sorted({int(x) for x in user_ids})
        if not ids:
            return []
        result = await self._session.execute(
            select(UserModel).where(UserModel.id.in_(ids))
        )
        rows = result.scalars().all()
        return [self._to_entity(r) for r in rows]

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

    def _list_filters(
        self,
        *,
        include_archived: bool,
        q: Optional[str],
        role: Optional[str],
        exclude_hidden_system: bool,
    ):
        conditions = []
        if not include_archived:
            conditions.append(UserModel.is_archived == False)
        if exclude_hidden_system:
            conditions.append(_hidden_system_clause())
        role_value = (role or "").strip()
        if role_value and role_value.lower() != "all":
            conditions.append(UserModel.role == role_value)
        needle = (q or "").strip()
        if needle:
            like = f"%{needle.lower()}%"
            conditions.append(
                or_(
                    func.lower(UserModel.email).like(like),
                    func.lower(func.coalesce(UserModel.display_name, "")).like(like),
                    func.lower(UserModel.role).like(like),
                )
            )
        return conditions

    async def list_page(
        self,
        *,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 50,
        q: Optional[str] = None,
        role: Optional[str] = None,
        exclude_hidden_system: bool = True,
    ) -> Tuple[Sequence[User], int]:
        conditions = self._list_filters(
            include_archived=include_archived,
            q=q,
            role=role,
            exclude_hidden_system=exclude_hidden_system,
        )
        count_q = select(func.count()).select_from(UserModel)
        list_q = select(UserModel).order_by(UserModel.id)
        if conditions:
            count_q = count_q.where(and_(*conditions))
            list_q = list_q.where(and_(*conditions))
        total = int((await self._session.execute(count_q)).scalar_one() or 0)
        list_q = list_q.offset(max(0, skip)).limit(max(1, min(200, limit)))
        rows = (await self._session.execute(list_q)).scalars().all()
        return [self._to_entity(r) for r in rows], total

    async def list_summary(
        self,
        *,
        include_archived: bool = False,
        exclude_hidden_system: bool = True,
    ) -> dict:
        conditions = self._list_filters(
            include_archived=include_archived,
            q=None,
            role=None,
            exclude_hidden_system=exclude_hidden_system,
        )
        base = select(
            func.count().label("total"),
            func.sum(func.cast(and_(UserModel.is_blocked == False, UserModel.is_archived == False), Integer)).label("active"),
            func.sum(func.cast(UserModel.is_blocked == True, Integer)).label("blocked"),
            func.sum(func.cast(UserModel.is_archived == True, Integer)).label("archived"),
        ).select_from(UserModel)
        if conditions:
            base = base.where(and_(*conditions))
        row = (await self._session.execute(base)).one()
        roles_q = select(UserModel.role, func.count()).select_from(UserModel)
        if conditions:
            roles_q = roles_q.where(and_(*conditions))
        roles_q = roles_q.group_by(UserModel.role).order_by(func.count().desc())
        role_rows = (await self._session.execute(roles_q)).all()
        return {
            "total": int(row.total or 0),
            "active": int(row.active or 0),
            "blocked": int(row.blocked or 0),
            "archived": int(row.archived or 0),
            "roles": [{"name": (name or "Не указано").strip() or "Не указано", "count": int(cnt)} for name, cnt in role_rows],
        }

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

    async def set_initials(self, user_id: int, initials: Optional[str]) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(initials=initials)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def set_active_session_jti(self, user_id: int, jti: Optional[str]) -> Optional[User]:
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(active_session_jti=jti)
        )
        await self._session.flush()
        return await self.get_by_id(user_id)

    async def list_active_session_jtis(self, user_id: int) -> list[str]:
        result = await self._session.execute(
            select(UserAuthSessionModel.jti)
            .where(UserAuthSessionModel.user_id == user_id)
            .order_by(UserAuthSessionModel.created_at.asc())
        )
        return [row[0] for row in result.all()]

    async def register_session_jti(self, user_id: int, jti: str, *, max_sessions: int) -> None:
        jti = (jti or "").strip()
        if not jti:
            return
        limit = max(1, int(max_sessions))
        await self._session.execute(
            delete(UserAuthSessionModel).where(UserAuthSessionModel.jti == jti)
        )
        self._session.add(UserAuthSessionModel(user_id=user_id, jti=jti))
        await self._session.flush()
        result = await self._session.execute(
            select(UserAuthSessionModel)
            .where(UserAuthSessionModel.user_id == user_id)
            .order_by(UserAuthSessionModel.created_at.asc())
        )
        rows = list(result.scalars().all())
        while len(rows) > limit:
            oldest = rows.pop(0)
            await self._session.delete(oldest)
        await self._session.flush()
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(active_session_jti=None)
        )

    async def remove_session_jti(self, user_id: int, jti: str) -> None:
        jti = (jti or "").strip()
        if not jti:
            return
        await self._session.execute(
            delete(UserAuthSessionModel).where(
                UserAuthSessionModel.user_id == user_id,
                UserAuthSessionModel.jti == jti,
            )
        )
        await self._session.flush()

    async def clear_all_session_jtis(self, user_id: int) -> None:
        await self._session.execute(
            delete(UserAuthSessionModel).where(UserAuthSessionModel.user_id == user_id)
        )
        await self._session.execute(
            update(UserModel).where(UserModel.id == user_id).values(active_session_jti=None)
        )
        await self._session.flush()
