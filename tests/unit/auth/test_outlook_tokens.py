from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from application.outlook_tokens import OutlookTokenService, hash_refresh_token
from infrastructure.jwt_service import (
    JWTService,
    OUTLOOK_TOKEN_AUD,
    OUTLOOK_TOKEN_SCOPE,
    OUTLOOK_TOKEN_TYP,
)
from infrastructure.models import OutlookRefreshTokenModel


class FakeUser:
    def __init__(self, user_id: int = 7):
        self.id = user_id
        self.azure_oid = "oid-1"
        self.email = "u@example.com"
        self.display_name = "User"
        self.is_blocked = False
        self.is_archived = False


class FakeUserRepo:
    def __init__(self, user: FakeUser):
        self.user = user

    async def get_by_id(self, user_id: int):
        return self.user if user_id == self.user.id else None

    async def get_by_azure_oid(self, azure_oid: str):
        return self.user if azure_oid == self.user.azure_oid else None

    async def create(self, *args, **kwargs):
        return self.user

    async def update_profile(self, *args, **kwargs):
        return self.user


class FakeSession:
    def __init__(self):
        self.rows: list[OutlookRefreshTokenModel] = []
        self._id = 1

    def add(self, row: OutlookRefreshTokenModel):
        if row.id is None:
            row.id = self._id
            self._id += 1
        self.rows.append(row)

    async def flush(self):
        return None

    async def scalar(self, statement):
        # simplistic: find by token_hash equality in compiled where — use last digest lookup helper
        return getattr(self, "_lookup", None)

    async def execute(self, statement):
        return None


@pytest.mark.asyncio
async def test_outlook_access_token_claims(monkeypatch):
    monkeypatch.setattr(
        "infrastructure.jwt_service.get_settings",
        lambda: SimpleNamespace(
            jwt_secret="x" * 32,
            jwt_algorithm="HS256",
            outlook_access_expire_minutes=10,
        ),
    )
    token = JWTService().create_outlook_access_token(
        7, "oid-1", jti="j1", device_id="device-12345", family_id="fam-1"
    )
    payload = JWTService().decode_token(token)
    assert payload is not None
    assert payload["typ"] == OUTLOOK_TOKEN_TYP
    assert payload["aud"] == OUTLOOK_TOKEN_AUD
    assert payload["scope"] == OUTLOOK_TOKEN_SCOPE
    assert payload["did"] == "device-12345"
    assert payload["sub"] == "7"


@pytest.mark.asyncio
async def test_refresh_rotation_and_reuse(monkeypatch):
    monkeypatch.setattr(
        "application.outlook_tokens.get_settings",
        lambda: SimpleNamespace(outlook_refresh_expire_days=30, outlook_access_expire_minutes=10),
    )
    monkeypatch.setattr(
        "infrastructure.jwt_service.get_settings",
        lambda: SimpleNamespace(
            jwt_secret="y" * 32,
            jwt_algorithm="HS256",
            outlook_access_expire_minutes=10,
        ),
    )
    user = FakeUser()
    session = FakeSession()
    svc = OutlookTokenService(session, FakeUserRepo(user), JWTService())  # type: ignore[arg-type]

    pair1 = await svc.issue_for_user(user, device_id="device-abcdef")
    assert pair1.refresh_token
    assert len(session.rows) == 1
    first = session.rows[0]
    assert first.token_hash == hash_refresh_token(pair1.refresh_token)

    async def scalar_lookup(statement):
        digest = hash_refresh_token(pair1.refresh_token)
        for row in session.rows:
            if row.token_hash == digest:
                return row
        return None

    session.scalar = scalar_lookup  # type: ignore[method-assign]
    pair2 = await svc.refresh(pair1.refresh_token, device_id="device-abcdef")
    assert pair2.refresh_token != pair1.refresh_token
    assert first.revoked_at is not None

    with pytest.raises(PermissionError, match="reuse"):
        await svc.refresh(pair1.refresh_token, device_id="device-abcdef")


@pytest.mark.asyncio
async def test_device_mismatch(monkeypatch):
    monkeypatch.setattr(
        "application.outlook_tokens.get_settings",
        lambda: SimpleNamespace(outlook_refresh_expire_days=30, outlook_access_expire_minutes=10),
    )
    monkeypatch.setattr(
        "infrastructure.jwt_service.get_settings",
        lambda: SimpleNamespace(
            jwt_secret="z" * 32,
            jwt_algorithm="HS256",
            outlook_access_expire_minutes=10,
        ),
    )
    user = FakeUser()
    session = FakeSession()
    svc = OutlookTokenService(session, FakeUserRepo(user), JWTService())  # type: ignore[arg-type]
    pair = await svc.issue_for_user(user, device_id="device-abcdef")

    async def scalar_lookup(statement):
        return session.rows[0]

    session.scalar = scalar_lookup  # type: ignore[method-assign]
    with pytest.raises(PermissionError, match="device"):
        await svc.refresh(pair.refresh_token, device_id="other-device-1")
