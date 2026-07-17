from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

import application.partner_report_confirmation_service as svc
from infrastructure.repository_partner_report_confirmations import (
    PartnerReportConfirmationRepository,
)


@pytest.mark.asyncio
async def test_invalidate_confirmations_after_time_entry_change_skips_empty():
    session = AsyncMock()
    n = await svc.invalidate_confirmations_after_time_entry_change(
        session, project_id="", work_date=date(2026, 6, 10)
    )
    assert n == 0
    n2 = await svc.invalidate_confirmations_after_time_entry_change(
        session, project_id="p1", work_date=None
    )
    assert n2 == 0


@pytest.mark.asyncio
async def test_invalidate_confirmations_after_time_entry_change_calls_repo(monkeypatch):
    session = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.invalidate_for_project_covering_date = AsyncMock(return_value=2)
    monkeypatch.setattr(svc, "PartnerReportConfirmationRepository", lambda _s: mock_repo)
    monkeypatch.setattr(svc, "invalidate_partner_confirmation_read_caches", lambda: None)
    n = await svc.invalidate_confirmations_after_time_entry_change(
        session, project_id="proj-1", work_date=date(2026, 6, 15)
    )
    assert n == 2
    mock_repo.invalidate_for_project_covering_date.assert_awaited_once_with(
        "proj-1", date(2026, 6, 15)
    )
    session.flush.assert_awaited()


@pytest.mark.asyncio
async def test_invalidate_for_project_covering_date_query_and_reset():
    session = AsyncMock()
    repo = PartnerReportConfirmationRepository(session)

    # First execute → select ids; second → delete signatures
    id_result = MagicMock()
    id_result.scalars.return_value.all.return_value = ["req-1"]
    del_result = MagicMock()
    session.execute = AsyncMock(side_effect=[id_result, del_result])

    row = MagicMock()
    row.status = "fully_confirmed"
    repo.get_request_by_id = AsyncMock(return_value=row)

    n = await repo.invalidate_for_project_covering_date("p1", date(2026, 6, 10))
    assert n == 1
    assert row.status == "pending_partners"
    assert session.execute.await_count == 2
    session.add.assert_called()
