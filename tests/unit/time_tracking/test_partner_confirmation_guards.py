from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import application.partner_report_confirmation_service as svc


@pytest.mark.asyncio
async def test_entry_counts_empty_rows():
    assert await svc._entry_counts_for_request_rows(AsyncMock(), []) == {}


@pytest.mark.asyncio
async def test_entry_counts_maps_periods(monkeypatch):
    session = AsyncMock()
    rows = [
        SimpleNamespace(id="a", project_id="p1", date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)),
        SimpleNamespace(id="b", project_id="p1", date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)),
    ]
    mock_repo = MagicMock()
    mock_repo.count_entries_by_project_periods = AsyncMock(
        return_value={("p1", date(2026, 1, 1), date(2026, 1, 31)): 4}
    )
    monkeypatch.setattr(svc, "TimeEntryRepository", lambda _s: mock_repo)
    out = await svc._entry_counts_for_request_rows(session, rows)
    assert out == {"a": 4, "b": 4}


@pytest.mark.asyncio
async def test_ensure_fully_confirmed_raises_403(monkeypatch):
    session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.has_fully_confirmed_for_project_period = AsyncMock(return_value=False)
    monkeypatch.setattr(svc, "PartnerReportConfirmationRepository", lambda _s: mock_repo)
    with pytest.raises(HTTPException) as exc:
        await svc.ensure_fully_confirmed_partner_period_or_403(
            session,
            project_id="p1",
            date_from=date(2026, 1, 1),
            date_to=date(2026, 1, 10),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_ensure_fully_confirmed_ok(monkeypatch):
    session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.has_fully_confirmed_for_project_period = AsyncMock(return_value=True)
    monkeypatch.setattr(svc, "PartnerReportConfirmationRepository", lambda _s: mock_repo)
    await svc.ensure_fully_confirmed_partner_period_or_403(
        session,
        project_id="p1",
        date_from=date(2026, 1, 1),
        date_to=date(2026, 1, 10),
    )
    mock_repo.has_fully_confirmed_for_project_period.assert_awaited_once()
