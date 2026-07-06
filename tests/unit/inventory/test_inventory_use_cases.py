from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from application.use_cases import CreateCategoryUseCase, GetHealthUseCase


@pytest.mark.unit
@pytest.mark.asyncio
async def test_inventory_health_ok():
    health_repo = AsyncMock()
    health_repo.check = AsyncMock(return_value=True)
    uc = GetHealthUseCase(health_repo)
    result = await uc.execute("inventory")
    assert result.status == "healthy"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_create_category():
    repo = AsyncMock()
    repo.create = AsyncMock(return_value="cat")
    uc = CreateCategoryUseCase(repo)
    result = await uc.execute(name="Laptops", sort_order=1)
    repo.create.assert_awaited_once_with(
        name="Laptops",
        description=None,
        sort_order=1,
        parent_id=None,
    )
    assert result == "cat"
