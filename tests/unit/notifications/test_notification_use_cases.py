from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from application.use_cases import CreateNotificationUseCase, GetHealthUseCase


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_health_degraded():
    health_repo = AsyncMock()
    health_repo.check = AsyncMock(return_value=False)
    uc = GetHealthUseCase(health_repo)
    result = await uc.execute("notifications")
    assert result.status == "degraded"
    assert result.service == "notifications"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_notification_generates_uuid():
    repo = AsyncMock()
    repo.create = AsyncMock(return_value="created")
    uc = CreateNotificationUseCase(repo)
    await uc.execute(title="Hi", description="Body", recipient_user_id=1)
    repo.create.assert_awaited_once()
    kwargs = repo.create.await_args.kwargs
    assert kwargs["title"] == "Hi"
    assert len(kwargs["uuid"]) == 36
