import pytest
from fastapi import HTTPException

from application.access_control import (
    ensure_can_patch_transfer_without_project_access,
    viewer_can_transfer_time_without_project_access,
)


def test_ensure_can_patch_transfer_only_for_manage_roles():
    ensure_can_patch_transfer_without_project_access({"role": "Партнер", "id": 1})

    with pytest.raises(HTTPException) as exc:
        ensure_can_patch_transfer_without_project_access({"role": "Сотрудник", "id": 2})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_viewer_can_transfer_admin_without_flag(monkeypatch):
    async def _fail(*_a, **_k):
        raise AssertionError("should not query TT user for admin")

    monkeypatch.setattr(
        "application.access_control.TimeTrackingUserRepository.get_by_auth_user_id",
        _fail,
    )
    assert await viewer_can_transfer_time_without_project_access(
        None,
        {"role": "Администратор", "id": 1},
    )
