from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from application.errors import UserAccessDenied
from application.use_cases import (
    ArchiveUserUseCase,
    AzureLoginUseCase,
    BlockUserUseCase,
    GetCurrentUserUseCase,
)
from domain.entities import User


def _user(**overrides) -> User:
    now = datetime.now(timezone.utc)
    base = dict(
        id=1,
        azure_oid="oid-1",
        email="u@kostalegal.com",
        display_name="User",
        picture=None,
        role="Employee",
        position=None,
        is_blocked=False,
        is_archived=False,
        time_tracking_role=None,
        desktop_background=None,
        initials=None,
        active_session_jti="legacy-jti",
        created_at=now,
        updated_at=now,
    )
    base.update(overrides)
    return User(**base)


class FakeRepo:
    def __init__(self, user: User):
        self.user = user
        self.active_jtis = ["jti-active"]
        self.cleared = False
        self.registered: list[str] = []

    async def get_by_azure_oid(self, azure_oid: str):
        return self.user if azure_oid == self.user.azure_oid else None

    async def get_by_id(self, user_id: int):
        return self.user if user_id == self.user.id else None

    async def create(self, *args, **kwargs):
        return self.user

    async def update_profile(self, *args, **kwargs):
        return self.user

    async def register_session_jti(self, user_id, jti, max_sessions=2):
        self.registered.append(jti)

    async def list_active_session_jtis(self, user_id):
        return list(self.active_jtis)

    async def set_blocked(self, user_id, is_blocked):
        self.user.is_blocked = is_blocked
        return self.user

    async def set_archived(self, user_id, is_archived):
        self.user.is_archived = is_archived
        return self.user

    async def clear_all_session_jtis(self, user_id):
        self.cleared = True
        self.active_jtis = []


class FakeTokens:
    def __init__(self, payload: dict | None):
        self.payload = payload

    def create_access_token(self, user_id, azure_oid, jti):
        return f"token-{user_id}-{jti}"

    def decode_token(self, access_token):
        return self.payload


@pytest.mark.asyncio
async def test_login_rejects_blocked_user(monkeypatch):
    monkeypatch.setattr(
        "application.use_cases.get_settings",
        lambda: SimpleNamespace(auth_max_concurrent_sessions=2),
    )
    repo = FakeRepo(_user(is_blocked=True))
    uc = AzureLoginUseCase(repo, FakeTokens(None))
    with pytest.raises(UserAccessDenied):
        await uc.execute("oid-1", "u@kostalegal.com", "User", None)
    assert repo.registered == []


@pytest.mark.asyncio
async def test_login_rejects_archived_user(monkeypatch):
    monkeypatch.setattr(
        "application.use_cases.get_settings",
        lambda: SimpleNamespace(auth_max_concurrent_sessions=2),
    )
    repo = FakeRepo(_user(is_archived=True))
    uc = AzureLoginUseCase(repo, FakeTokens(None))
    with pytest.raises(UserAccessDenied):
        await uc.execute("oid-1", "u@kostalegal.com", "User", None)


@pytest.mark.asyncio
async def test_get_current_user_rejects_blocked():
    repo = FakeRepo(_user(is_blocked=True))
    tokens = FakeTokens({"sub": "1", "jti": "jti-active"})
    uc = GetCurrentUserUseCase(repo, tokens)
    assert await uc.execute("tok") is None


@pytest.mark.asyncio
async def test_get_current_user_rejects_archived():
    repo = FakeRepo(_user(is_archived=True))
    tokens = FakeTokens({"sub": "1", "jti": "jti-active"})
    uc = GetCurrentUserUseCase(repo, tokens)
    assert await uc.execute("tok") is None


@pytest.mark.asyncio
async def test_get_current_user_allows_active():
    repo = FakeRepo(_user())
    tokens = FakeTokens({"sub": "1", "jti": "jti-active"})
    uc = GetCurrentUserUseCase(repo, tokens)
    user = await uc.execute("tok")
    assert user is not None
    assert user.id == 1


@pytest.mark.asyncio
async def test_block_clears_sessions():
    repo = FakeRepo(_user())
    user = await BlockUserUseCase(repo).execute(1, True)
    assert user is not None
    assert user.is_blocked is True
    assert repo.cleared is True
    assert repo.active_jtis == []


@pytest.mark.asyncio
async def test_unblock_does_not_clear_sessions():
    repo = FakeRepo(_user(is_blocked=True))
    repo.active_jtis = ["keep-me"]
    user = await BlockUserUseCase(repo).execute(1, False)
    assert user is not None
    assert user.is_blocked is False
    assert repo.cleared is False
    assert repo.active_jtis == ["keep-me"]


@pytest.mark.asyncio
async def test_archive_clears_sessions():
    repo = FakeRepo(_user())
    user = await ArchiveUserUseCase(repo).execute(1, True)
    assert user is not None
    assert user.is_archived is True
    assert repo.cleared is True
