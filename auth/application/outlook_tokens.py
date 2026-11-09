from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from application.ports import UserRepositoryPort
from domain.entities import User
from domain.roles import Role
from infrastructure.config import get_settings
from infrastructure.jwt_service import JWTService
from infrastructure.models import OutlookRefreshTokenModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


@dataclass(frozen=True)
class OutlookTokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "bearer"
    scope: str = "fm.outlook.upload"


class OutlookTokenService:
    def __init__(
        self,
        session: AsyncSession,
        user_repo: UserRepositoryPort,
        token_service: JWTService | None = None,
    ):
        self._session = session
        self._user_repo = user_repo
        self._tokens = token_service or JWTService()

    async def issue_for_user(self, user: User, *, device_id: str) -> OutlookTokenPair:
        device = (device_id or "").strip()[:128]
        if len(device) < 8:
            raise ValueError("device_id required")
        family_id = str(uuid.uuid4())
        return await self._mint_pair(user, device_id=device, family_id=family_id)

    async def ensure_user(
        self,
        *,
        azure_oid: str,
        email: str,
        display_name: str | None,
        picture: str | None,
    ) -> User:
        user = await self._user_repo.get_by_azure_oid(azure_oid)
        if not user:
            return await self._user_repo.create(
                azure_oid, email, display_name, picture, Role.EMPLOYEE.value
            )
        dn = (display_name or "").strip() or None
        pic = picture.strip() if isinstance(picture, str) and picture.strip() else None
        if dn is not None or pic is not None:
            updated = await self._user_repo.update_profile(user.id, dn, pic, None)
            if updated is not None:
                return updated
        return user

    async def refresh(self, raw_refresh: str, *, device_id: str) -> OutlookTokenPair:
        device = (device_id or "").strip()[:128]
        raw = (raw_refresh or "").strip()
        if len(device) < 8 or not raw:
            raise PermissionError("invalid refresh")
        digest = hash_refresh_token(raw)
        row = await self._session.scalar(
            select(OutlookRefreshTokenModel).where(OutlookRefreshTokenModel.token_hash == digest)
        )
        if row is None:
            raise PermissionError("invalid refresh")
        if row.revoked_at is not None:
            await self._revoke_family(row.family_id)
            await self._session.flush()
            raise PermissionError("refresh reuse")
        if row.device_id != device:
            raise PermissionError("device mismatch")
        if row.expires_at < _utcnow():
            row.revoked_at = _utcnow()
            await self._session.flush()
            raise PermissionError("refresh expired")

        user = await self._user_repo.get_by_id(row.user_id)
        if not user or user.is_blocked or user.is_archived:
            await self._revoke_family(row.family_id)
            await self._session.flush()
            raise PermissionError("user blocked")

        row.revoked_at = _utcnow()
        row.last_used_at = _utcnow()
        await self._session.flush()
        return await self._mint_pair(user, device_id=device, family_id=row.family_id)

    async def logout(self, *, user_id: int, device_id: str | None = None, family_id: str | None = None) -> None:
        now = _utcnow()
        stmt = (
            update(OutlookRefreshTokenModel)
            .where(
                OutlookRefreshTokenModel.user_id == user_id,
                OutlookRefreshTokenModel.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        if family_id:
            stmt = stmt.where(OutlookRefreshTokenModel.family_id == family_id)
        if device_id:
            stmt = stmt.where(OutlookRefreshTokenModel.device_id == device_id.strip()[:128])
        await self._session.execute(stmt)
        await self._session.flush()

    async def _mint_pair(self, user: User, *, device_id: str, family_id: str) -> OutlookTokenPair:
        settings = get_settings()
        raw = new_refresh_token()
        digest = hash_refresh_token(raw)
        expires = _utcnow() + timedelta(days=settings.outlook_refresh_expire_days)
        self._session.add(
            OutlookRefreshTokenModel(
                user_id=user.id,
                family_id=family_id,
                token_hash=digest,
                device_id=device_id,
                expires_at=expires,
            )
        )
        await self._session.flush()
        access = self._tokens.create_outlook_access_token(
            user.id,
            user.azure_oid,
            jti=str(uuid.uuid4()),
            device_id=device_id,
            family_id=family_id,
        )
        return OutlookTokenPair(
            access_token=access,
            refresh_token=raw,
            expires_in=int(settings.outlook_access_expire_minutes * 60),
        )

    async def _revoke_family(self, family_id: str) -> None:
        await self._session.execute(
            update(OutlookRefreshTokenModel)
            .where(
                OutlookRefreshTokenModel.family_id == family_id,
                OutlookRefreshTokenModel.revoked_at.is_(None),
            )
            .values(revoked_at=_utcnow())
        )
