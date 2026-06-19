from unittest.mock import AsyncMock, patch

import pytest

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

import application.auth_user_directory as auth_user_directory
from application.auth_user_directory import fetch_auth_user_for_tt_provision


@pytest.mark.asyncio
async def test_fetch_auth_user_for_tt_provision_uses_public_fallback():
    detail = {"id": 5, "email": "a@b.c", "time_tracking_role": "user", "position": "Lawyer"}
    public = {"id": 5, "email": "a@b.c", "display_name": "Ann", "position": "Lawyer"}

    with patch.object(
        auth_user_directory,
        "fetch_auth_user_detail",
        new=AsyncMock(return_value=None),
    ), patch.object(
        auth_user_directory,
        "fetch_auth_user_public",
        new=AsyncMock(return_value=public),
    ):
        out = await fetch_auth_user_for_tt_provision("Bearer t", 5)

    assert out is not None
    assert out["email"] == "a@b.c"
    assert out["time_tracking_role"] == "user"

    with patch.object(
        auth_user_directory,
        "fetch_auth_user_detail",
        new=AsyncMock(return_value=detail),
    ), patch.object(
        auth_user_directory,
        "fetch_auth_user_public",
        new=AsyncMock(return_value=public),
    ):
        out_detail = await fetch_auth_user_for_tt_provision("Bearer t", 5)

    assert out_detail == detail
