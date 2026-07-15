from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from application.partner_confirmation_team_scope import (
    list_report_auth_user_ids_for_project_periods,
    periods_overlapping_team_entries,
)


@pytest.mark.asyncio
async def test_list_report_auth_user_ids_empty_periods():
    session = AsyncMock()
    out = await list_report_auth_user_ids_for_project_periods(session, [])
    assert out == {}
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_periods_overlapping_team_entries_filters():
    session = AsyncMock()
    result = MagicMock()
    result.all.return_value = [
        ("p1", date(2026, 6, 10)),
        ("p1", date(2026, 6, 20)),
        ("p2", date(2026, 7, 1)),
    ]
    session.execute = AsyncMock(return_value=result)

    periods = [
        ("p1", date(2026, 6, 1), date(2026, 6, 15)),
        ("p1", date(2026, 6, 16), date(2026, 6, 30)),
        ("p2", date(2026, 8, 1), date(2026, 8, 31)),
    ]
    hits = await periods_overlapping_team_entries(session, periods, {10, 11})
    assert ("p1", date(2026, 6, 1), date(2026, 6, 15)) in hits
    assert ("p1", date(2026, 6, 16), date(2026, 6, 30)) in hits
    assert ("p2", date(2026, 8, 1), date(2026, 8, 31)) not in hits


@pytest.mark.asyncio
async def test_periods_overlapping_empty_team():
    session = AsyncMock()
    hits = await periods_overlapping_team_entries(
        session,
        [("p1", date(2026, 1, 1), date(2026, 1, 31))],
        set(),
    )
    assert hits == set()
    session.execute.assert_not_called()
