import pytest

from support.service_path import ensure_service_in_path


def test_build_tt_upsert_uses_stub_not_auth_pii():
    ensure_service_in_path("gateway")
    from presentation.time_tracking_user_provision import build_tt_upsert_payload_from_auth_record

    record = {
        "id": 7,
        "email": "u@example.com",
        "display_name": "User",
        "picture": "http://x/y.png",
        "position": "Partner",
        "time_tracking_role": "user",
        "is_blocked": False,
        "is_archived": False,
    }
    payload = build_tt_upsert_payload_from_auth_record(record)
    assert payload is not None
    assert payload["email"] == "auth-user-7@tt.local"
    assert payload["display_name"] is None
    assert payload["picture"] is None
    assert payload["position"] is None
    assert payload["role"] == "user"


def test_build_tt_upsert_works_without_auth_email():
    ensure_service_in_path("gateway")
    from presentation.time_tracking_user_provision import build_tt_upsert_payload_from_auth_record

    payload = build_tt_upsert_payload_from_auth_record(
        {"id": 11, "time_tracking_role": "manager"},
        default_tt_role="manager",
    )
    assert payload is not None
    assert payload["email"] == "auth-user-11@tt.local"
    assert payload["role"] == "manager"


def test_build_auth_profile_sync_payload_no_pii_dual_write():
    ensure_service_in_path("gateway")
    from presentation.time_tracking_user_provision import (
        build_auth_profile_sync_payload_from_auth_record,
    )

    record = {
        "id": 8,
        "email": "p@example.com",
        "display_name": "Partner",
        "position": "Partner",
        "time_tracking_role": "manager",
        "is_blocked": True,
        "is_archived": False,
    }
    payload = build_auth_profile_sync_payload_from_auth_record(record, default_tt_role="manager")
    assert payload is not None
    assert payload["email"] == "auth-user-8@tt.local"
    assert payload["displayName"] is None
    assert "position" not in payload
    assert "updatePosition" not in payload
    assert payload["isBlocked"] is True
    assert payload["role"] == "manager"


@pytest.mark.asyncio
async def test_schema_patch_runner_skips_logged_patch(monkeypatch):
    from unittest.mock import AsyncMock

    ensure_service_in_path("time_tracking")
    from backend_common import schema_patch_runner as runner

    calls: list[str] = []

    async def fake_patch(_conn):
        calls.append("ran")

    applied: set[str] = set()

    async def fake_is_applied(_conn, name: str, *, table_name: str = "") -> bool:
        return name in applied

    async def fake_mark(_conn, name: str, *, table_name: str = "") -> None:
        applied.add(name)

    monkeypatch.setattr(runner, "_is_patch_applied", fake_is_applied)
    monkeypatch.setattr(runner, "_mark_patch_applied", fake_mark)
    monkeypatch.setattr(runner, "ensure_patch_log_table", AsyncMock())

    conn = AsyncMock()
    await runner.apply_registered_schema_patches(
        conn,
        [("demo_patch", fake_patch)],
        table_name="tt_schema_patch_log",
        log_prefix="TT",
    )
    assert calls == ["ran"]

    calls.clear()
    await runner.apply_registered_schema_patches(
        conn,
        [("demo_patch", fake_patch)],
        table_name="tt_schema_patch_log",
        log_prefix="TT",
    )
    assert calls == []


@pytest.mark.asyncio
async def test_clear_tt_role_archives_instead_of_delete():
    from unittest.mock import AsyncMock, patch

    ensure_service_in_path("gateway")
    from presentation import time_tracking_user_provision as mod

    record = {
        "id": 42,
        "email": "gone@example.com",
        "display_name": "Gone",
        "time_tracking_role": "",
        "is_blocked": False,
        "is_archived": False,
    }

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.patch.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch.object(mod, "_time_tracking_base_url", return_value="http://tt"), patch.object(
        mod, "_tt_user_exists", new=AsyncMock(return_value=True)
    ), patch("presentation.time_tracking_user_provision.httpx.AsyncClient", return_value=mock_client):
        await mod.upsert_time_tracking_user_from_auth_record(record, "Bearer t")

    mock_client.delete.assert_not_awaited()
    mock_client.patch.assert_awaited_once()
    args, kwargs = mock_client.patch.await_args
    assert args[0] == "http://tt/users/42/lifecycle-flags"
    assert kwargs["json"]["isArchived"] is True
    assert kwargs["json"]["isBlocked"] is True


@pytest.mark.asyncio
async def test_upsert_sync_existing_user_uses_auth_profile_patch_without_pii():
    from unittest.mock import AsyncMock, patch

    ensure_service_in_path("gateway")
    from presentation import time_tracking_user_provision as mod

    record = {
        "id": 99,
        "email": "sync@example.com",
        "display_name": "Synced",
        "time_tracking_role": "user",
        "is_blocked": False,
        "is_archived": False,
    }

    mock_client = AsyncMock()
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_client.get.return_value = mock_response
    mock_client.patch.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    with patch.object(mod, "_time_tracking_base_url", return_value="http://tt"), patch.object(
        mod, "_tt_user_exists", new=AsyncMock(return_value=True)
    ), patch("presentation.time_tracking_user_provision.httpx.AsyncClient", return_value=mock_client):
        await mod.upsert_time_tracking_user_from_auth_record(record, "Bearer t")

    mock_client.patch.assert_awaited_once()
    args, kwargs = mock_client.patch.await_args
    assert args[0] == "http://tt/users/99/auth-profile"
    assert kwargs["json"]["email"] == "auth-user-99@tt.local"
    assert kwargs["json"]["displayName"] is None
    mock_client.post.assert_not_awaited()


def test_hydrate_tt_user_prefers_auth_pii_keeps_tt_position():
    ensure_service_in_path("time_tracking")
    from application.auth_user_pii import AuthUserPii, hydrate_tt_user

    class Row:
        auth_user_id = 5
        email = "auth-user-5@tt.local"
        display_name = None
        picture = None
        position = "TT Counsel"
        is_blocked = False

    pii = {
        5: AuthUserPii(
            email="real@example.com",
            display_name="Real Name",
            picture="pic",
            position="Auth Partner",
        )
    }
    hydrated = hydrate_tt_user(Row(), pii)
    assert hydrated.email == "real@example.com"
    assert hydrated.display_name == "Real Name"
    assert hydrated.picture == "pic"
    assert hydrated.position == "TT Counsel"


def test_hydrate_manual_tt_user_keeps_local_pii():
    ensure_service_in_path("time_tracking")
    from application.auth_user_pii import AuthUserPii, hydrate_tt_user
    from application.manual_tt_users import MANUAL_TT_USER_AUTH_ID_FLOOR

    class Row:
        auth_user_id = MANUAL_TT_USER_AUTH_ID_FLOOR
        email = "manual@local.test"
        display_name = "Manual"
        picture = None
        position = "Clerk"

    pii = {
        MANUAL_TT_USER_AUTH_ID_FLOOR: AuthUserPii(
            email="should-not-apply@example.com",
            display_name="Nope",
            picture=None,
            position="X",
        )
    }
    out = hydrate_tt_user(Row(), pii)
    assert out is not None
    assert out.email == "manual@local.test"
    assert out.display_name == "Manual"
    assert out.position == "Clerk"
