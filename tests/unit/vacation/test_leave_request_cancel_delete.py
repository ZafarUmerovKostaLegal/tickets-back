from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from application.kind_legend import KIND_BY_KEY
from application.leave_request_service import (
    CANCEL_AFTER_APPROVE_REASON,
    WITHDRAW_REASON,
    apply_decision,
    cancel_leave_request,
    delete_leave_request,
)
from infrastructure.models import (
    LEAVE_STATUS_APPROVED,
    LEAVE_STATUS_CANCELLED,
    LEAVE_STATUS_DECLINED,
    LEAVE_STATUS_PENDING,
    AbsenceDay,
    LeaveRequest,
)
from infrastructure.orm_base import Base

EMPLOYEE_ID = 42
PARTNER_ID = 7


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _make_request(session: AsyncSession, *, status: str = LEAVE_STATUS_PENDING) -> LeaveRequest:
    req = LeaveRequest(
        employee_user_id=EMPLOYEE_ID,
        employee_full_name="Aziz Akhmadjanov",
        employee_email="aziz@example.com",
        employee_position="Associate",
        partner_user_id=PARTNER_ID,
        partner_full_name="Zafar Umerov",
        partner_email="zafar@example.com",
        kind_code=KIND_BY_KEY["annual_vacation"],
        date_from=date(2026, 6, 22),
        date_to=date(2026, 6, 24),
        days_count=3,
        reason=None,
        status=status,
        created_at=datetime.now(timezone.utc),
    )
    session.add(req)
    await session.flush()
    return req


async def _absence_days(session: AsyncSession, request_id: int) -> list[AbsenceDay]:
    rows = await session.execute(
        select(AbsenceDay).where(AbsenceDay.leave_request_id == request_id)
    )
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_withdraw_pending_marks_cancelled(session: AsyncSession):
    req = await _make_request(session)

    out = await cancel_leave_request(session, req, cancelled_by_user_id=EMPLOYEE_ID, reason=None)

    assert out.status == LEAVE_STATUS_CANCELLED
    assert out.decision_reason == WITHDRAW_REASON
    assert out.decided_by_user_id == EMPLOYEE_ID
    assert out.decision_at is not None


@pytest.mark.asyncio
async def test_cancel_approved_removes_schedule_days(session: AsyncSession):
    req = await _make_request(session)
    await apply_decision(
        session,
        req,
        decided_by_user_id=PARTNER_ID,
        approve=True,
        decision_reason=None,
    )
    assert req.status == LEAVE_STATUS_APPROVED
    assert len(await _absence_days(session, req.id)) == 3

    out = await cancel_leave_request(session, req, cancelled_by_user_id=EMPLOYEE_ID, reason=None)

    assert out.status == LEAVE_STATUS_CANCELLED
    assert out.decision_reason == CANCEL_AFTER_APPROVE_REASON
    assert await _absence_days(session, req.id) == []


@pytest.mark.asyncio
async def test_cancel_keeps_employee_comment(session: AsyncSession):
    req = await _make_request(session)

    out = await cancel_leave_request(
        session,
        req,
        cancelled_by_user_id=EMPLOYEE_ID,
        reason="  Планы изменились  ",
    )

    assert out.decision_reason == "Планы изменились"


@pytest.mark.asyncio
async def test_cancel_rejects_final_status(session: AsyncSession):
    req = await _make_request(session, status=LEAVE_STATUS_DECLINED)

    with pytest.raises(ValueError):
        await cancel_leave_request(session, req, cancelled_by_user_id=EMPLOYEE_ID, reason=None)


@pytest.mark.asyncio
async def test_delete_removes_request_and_days(session: AsyncSession):
    req = await _make_request(session)
    await apply_decision(
        session,
        req,
        decided_by_user_id=PARTNER_ID,
        approve=True,
        decision_reason=None,
    )
    request_id = req.id

    await delete_leave_request(session, req)
    await session.flush()

    assert await _absence_days(session, request_id) == []
    rows = await session.execute(select(LeaveRequest).where(LeaveRequest.id == request_id))
    assert rows.scalar_one_or_none() is None
