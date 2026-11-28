from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import OutlookPairingModel

PAIRING_TTL_SECONDS = 600
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


@dataclass(frozen=True)
class PairingStart:
    pair_id: str
    code: str
    secret: str
    expires_in: int


@dataclass(frozen=True)
class PairingInfo:
    code: str
    device: str
    expires_in: int
    confirmed: bool


class PairingNotFound(LookupError):
    pass


def normalize_code(raw: str) -> str:
    return "".join(ch for ch in (raw or "").upper() if ch.isalnum())[:CODE_LENGTH]


def display_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LENGTH else code


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


class OutlookPairingService:
    def __init__(self, session: AsyncSession, now=datetime.utcnow):
        self._session = session
        self._now = now

    async def _purge(self) -> None:
        await self._session.execute(delete(OutlookPairingModel).where(OutlookPairingModel.expires_at < self._now()))

    async def _by_code(self, code: str) -> OutlookPairingModel:
        key = normalize_code(code)
        row = None
        if len(key) == CODE_LENGTH:
            row = (await self._session.execute(
                select(OutlookPairingModel).where(OutlookPairingModel.code == key)
            )).scalars().one_or_none()
        if row is None or row.expires_at < self._now():
            raise PairingNotFound("Код не найден или устарел")
        return row

    async def start(self, device: str = "") -> PairingStart:
        await self._purge()
        secret = secrets.token_urlsafe(32)
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        row = OutlookPairingModel(
            id=uuid.uuid4().hex,
            code=code,
            secret_hash=_hash(secret),
            device=(device or "")[:300],
            created_at=self._now(),
            expires_at=self._now() + timedelta(seconds=PAIRING_TTL_SECONDS),
        )
        self._session.add(row)
        await self._session.flush()
        return PairingStart(pair_id=row.id, code=display_code(code), secret=secret, expires_in=PAIRING_TTL_SECONDS)

    async def info(self, code: str) -> PairingInfo:
        row = await self._by_code(code)
        left = max(0, int((row.expires_at - self._now()).total_seconds()))
        return PairingInfo(code=display_code(row.code), device=row.device, expires_in=left, confirmed=row.user_id is not None)

    async def confirm(self, code: str, user_id: int) -> None:
        row = await self._by_code(code)
        if row.user_id is not None and row.user_id != user_id:
            raise PairingNotFound("Код уже подтверждён другим пользователем")
        row.user_id = user_id
        row.confirmed_at = self._now()
        await self._session.flush()

    async def poll(self, pair_id: str, secret: str) -> int | None:
        """Return the approving user once (the code is then spent), None while still pending."""
        row = await self._session.get(OutlookPairingModel, (pair_id or "")[:32])
        if row is None or row.expires_at < self._now() or not hmac.compare_digest(row.secret_hash, _hash(secret or "")):
            raise PairingNotFound("Код не найден или устарел")
        if row.user_id is None:
            return None
        user_id = row.user_id
        await self._session.delete(row)
        await self._session.flush()
        return user_id
