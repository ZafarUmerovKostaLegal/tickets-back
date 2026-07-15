import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from application.partner_report_confirmation_service import (
    reconcile_confirmation_if_complete,
    _reconcile_pending_rows,
)


@pytest.mark.asyncio
async def test_reconcile_marks_complete():
    conf = AsyncMock()
    conf.mark_fully_confirmed = AsyncMock()
    row = SimpleNamespace(
        id="r1",
        status="pending_partners",
        signatures=[SimpleNamespace(partner_auth_user_id=1), SimpleNamespace(partner_auth_user_id=2)],
    )
    assert await reconcile_confirmation_if_complete(conf, row, [1, 2]) is True
    conf.mark_fully_confirmed.assert_awaited_once_with("r1")
    assert row.status == "fully_confirmed"


@pytest.mark.asyncio
async def test_reconcile_skips_incomplete_and_already_confirmed():
    conf = AsyncMock()
    incomplete = SimpleNamespace(
        id="r2",
        status="pending_partners",
        signatures=[SimpleNamespace(partner_auth_user_id=1)],
    )
    assert await reconcile_confirmation_if_complete(conf, incomplete, [1, 2]) is False
    conf.mark_fully_confirmed.assert_not_called()

    done = SimpleNamespace(id="r3", status="fully_confirmed", signatures=[])
    assert await reconcile_confirmation_if_complete(conf, done, [1]) is False


@pytest.mark.asyncio
async def test_reconcile_pending_rows_batch():
    conf = AsyncMock()
    conf.mark_fully_confirmed = AsyncMock()
    rows = [
        SimpleNamespace(
            id="a",
            status="pending_partners",
            project_id="p1",
            signatures=[SimpleNamespace(partner_auth_user_id=9)],
        ),
        SimpleNamespace(
            id="b",
            status="pending_partners",
            project_id="p2",
            signatures=[SimpleNamespace(partner_auth_user_id=1)],
        ),
    ]
    changed = await _reconcile_pending_rows(conf, rows, {"p1": [9], "p2": [1, 2]})
    assert changed is True
    conf.mark_fully_confirmed.assert_awaited_once_with("a")
