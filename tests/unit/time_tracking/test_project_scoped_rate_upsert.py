"""Project-scoped rate upsert must not wipe dated history."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import application.project_billable_rate_sync as sync_mod
from application.project_billable_rate_sync import upsert_user_project_scoped_billable_rate


def _rate(
    *,
    rid: str,
    amount: str,
    valid_from: date | None,
    valid_to: date | None,
    project_id: str = "p1",
):
    return SimpleNamespace(
        id=rid,
        amount=Decimal(amount),
        currency="USD",
        valid_from=valid_from,
        valid_to=valid_to,
        applies_to_project_id=project_id,
        rate_kind="billable",
    )


@pytest.mark.asyncio
async def test_upsert_updates_effective_rate_without_clearing_dates():
    old = _rate(
        rid="old",
        amount="100",
        valid_from=None,
        valid_to=date(2099, 1, 1),  # closed in the past relative to far-future open rate
    )
    # Force "old" closed and "new" current regardless of wall clock:
    old.valid_to = date(2020, 1, 1)
    new = _rate(
        rid="new",
        amount="120",
        valid_from=date(2020, 1, 2),
        valid_to=None,
    )
    hr = AsyncMock()
    hr.list_by_user_and_kind = AsyncMock(return_value=[old, new])
    hr.update = AsyncMock(return_value=new)
    hr.create = AsyncMock()
    hr.delete = AsyncMock()

    with patch.object(sync_mod, "HourlyRateRepository", return_value=hr):
        await upsert_user_project_scoped_billable_rate(
            AsyncMock(),
            auth_user_id=1,
            project_id="p1",
            amount=Decimal("150"),
            currency="USD",
            valid_from=None,
            valid_to=None,
        )

    hr.update.assert_awaited_once()
    args = hr.update.await_args.kwargs
    assert args["rate_id"] == "new"
    assert args["patch"]["amount"] == Decimal("150")
    assert "valid_from" not in args["patch"]
    assert "valid_to" not in args["patch"]
    hr.create.assert_not_awaited()
    hr.delete.assert_not_awaited()
