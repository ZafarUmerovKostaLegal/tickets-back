from datetime import date

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.database import Base
from infrastructure.repository_partner_report_confirmations import PartnerReportConfirmationRepository


@pytest.mark.asyncio
async def test_history_records_submission_priority_signatures_and_reset():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        repo = PartnerReportConfirmationRepository(session)
        req = await repo.upsert_submit(
            snapshot_id="s1", project_id="p1", date_from=date(2026, 11, 1), date_to=date(2026, 11, 30),
            title="Report", submitted_by_auth_user_id=7,
        )
        assert await repo.set_review_priority(req.id, "red", actor_auth_user_id=7)
        assert await repo.set_review_priority(req.id, "red", actor_auth_user_id=7)  # same value: no new line
        await repo.add_signature(req.id, 11)
        await repo.mark_fully_confirmed(req.id)
        await session.flush()
        await repo.invalidate_for_project_covering_date("p1", date(2026, 11, 15))
        await repo.upsert_submit(
            snapshot_id="s1", project_id="p1", date_from=date(2026, 11, 1), date_to=date(2026, 11, 30),
            title="Report", submitted_by_auth_user_id=7,
        )
        await session.commit()

        events = await repo.list_events(req.id)
        assert [(e.kind, e.from_value, e.to_value, e.actor_auth_user_id) for e in events] == [
            ("submitted", "", "yellow", 7),
            ("priority", "yellow", "red", 7),
            ("signed", "", "11", 11),
            ("confirmed", "pending_partners", "fully_confirmed", None),
            ("reset", "fully_confirmed", "pending_partners", None),
            ("resubmitted", "pending_partners", "yellow", 7),
            ("priority", "red", "yellow", 7),
        ]
        last = await repo.last_priority_change_by_request_ids([req.id])
        assert last[req.id].kind == "priority" and last[req.id].to_value == "yellow"
    await engine.dispose()
