import pytest

from support.service_path import ensure_service_in_path


def test_build_auth_profile_sync_payload_keeps_position_optional():
    ensure_service_in_path("gateway")
    from presentation.time_tracking_user_provision import (
        build_auth_profile_sync_payload_from_auth_record,
    )

    record = {
        "id": 7,
        "email": "u@example.com",
        "display_name": "User",
        "time_tracking_role": "user",
        "is_blocked": False,
        "is_archived": False,
    }
    payload = build_auth_profile_sync_payload_from_auth_record(record)
    assert payload is not None
    assert payload["email"] == "u@example.com"
    assert payload["displayName"] == "User"
    assert "position" not in payload
    assert "updatePosition" not in payload


def test_build_auth_profile_sync_payload_includes_position_when_present():
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
    assert payload["position"] == "Partner"
    assert payload["updatePosition"] is True
    assert payload["isBlocked"] is True


@pytest.mark.asyncio
async def test_schema_patch_runner_skips_logged_patch(monkeypatch):
    from unittest.mock import AsyncMock

    ensure_service_in_path("time_tracking")
    from infrastructure import schema_patch_runner as runner

    calls: list[str] = []

    async def fake_patch(_conn):
        calls.append("ran")

    applied: set[str] = set()

    async def fake_is_applied(_conn, name: str) -> bool:
        return name in applied

    async def fake_mark(_conn, name: str) -> None:
        applied.add(name)

    monkeypatch.setattr(runner, "_is_patch_applied", fake_is_applied)
    monkeypatch.setattr(runner, "_mark_patch_applied", fake_mark)
    monkeypatch.setattr(runner, "ensure_patch_log_table", AsyncMock())

    conn = AsyncMock()
    await runner.apply_registered_schema_patches(conn, [("demo_patch", fake_patch)])
    assert calls == ["ran"]

    calls.clear()
    await runner.apply_registered_schema_patches(conn, [("demo_patch", fake_patch)])
    assert calls == []


@pytest.mark.asyncio
async def test_upsert_sync_existing_user_uses_auth_profile_patch():
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
    assert kwargs["json"]["email"] == "sync@example.com"
    mock_client.post.assert_not_awaited()
