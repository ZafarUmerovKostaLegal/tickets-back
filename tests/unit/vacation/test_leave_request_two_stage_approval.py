from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from application.kind_legend import KIND_BY_KEY
from application.leave_request_service import (
    LEAVE_PDF_DOC_VERSION,
    apply_final_decision,
    apply_partner_decision,
    ensure_current_pdf,
)
from infrastructure.config import get_settings
from infrastructure.email_action_token import (
    STAGE_FINAL,
    STAGE_PARTNER,
    sign_email_action_token,
    verify_email_action_token,
)
from infrastructure.models import (
    LEAVE_STATUS_APPROVED,
    LEAVE_STATUS_DECLINED,
    LEAVE_STATUS_PENDING,
    LEAVE_STATUS_PENDING_FINAL,
    AbsenceDay,
    LeaveRequest,
)
from infrastructure.orm_base import Base

EMPLOYEE_ID = 42
PARTNER_ID = 7
MANAGING_PARTNER_ID = 3
SECRET = "test-secret"


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
        employee_full_name="Zafar Umerov",
        employee_email="zafar@example.com",
        employee_position="Associate",
        partner_user_id=PARTNER_ID,
        partner_full_name="Nail Hassanov",
        partner_email="nail@example.com",
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
    rows = await session.execute(select(AbsenceDay).where(AbsenceDay.leave_request_id == request_id))
    return list(rows.scalars().all())


@pytest.mark.asyncio
async def test_partner_approval_waits_for_managing_partner(session: AsyncSession):
    req = await _make_request(session)

    out = await apply_partner_decision(
        session,
        req,
        decided_by_user_id=PARTNER_ID,
        approve=True,
        decision_reason="Согласовано",
    )

    assert out.status == LEAVE_STATUS_PENDING_FINAL
    assert out.decided_by_user_id == PARTNER_ID
    assert out.decision_reason == "Согласовано"
    assert out.final_decision_at is None
    # Дни появляются в графике только после финального решения.
    assert await _absence_days(session, req.id) == []


@pytest.mark.asyncio
async def test_partner_decline_annuls_request(session: AsyncSession):
    req = await _make_request(session)

    out = await apply_partner_decision(
        session,
        req,
        decided_by_user_id=PARTNER_ID,
        approve=False,
        decision_reason="Загруженность",
    )

    assert out.status == LEAVE_STATUS_DECLINED
    assert out.final_decision_at is None
    assert await _absence_days(session, req.id) == []


@pytest.mark.asyncio
async def test_final_approval_opens_schedule_days(session: AsyncSession):
    req = await _make_request(session, status=LEAVE_STATUS_PENDING_FINAL)

    out = await apply_final_decision(
        session,
        req,
        decided_by_user_id=MANAGING_PARTNER_ID,
        approve=True,
        decision_reason=None,
    )

    assert out.status == LEAVE_STATUS_APPROVED
    assert out.final_decided_by_user_id == MANAGING_PARTNER_ID
    assert out.final_decision_at is not None
    assert len(await _absence_days(session, req.id)) == 3


@pytest.mark.asyncio
async def test_final_decline_annuls_after_partner_approval(session: AsyncSession):
    req = await _make_request(session)
    await apply_partner_decision(
        session,
        req,
        decided_by_user_id=PARTNER_ID,
        approve=True,
        decision_reason=None,
    )

    out = await apply_final_decision(
        session,
        req,
        decided_by_user_id=MANAGING_PARTNER_ID,
        approve=False,
        decision_reason="Нужен на проекте",
    )

    assert out.status == LEAVE_STATUS_DECLINED
    assert out.final_decision_reason == "Нужен на проекте"
    assert out.decision_reason is None
    assert await _absence_days(session, req.id) == []


@pytest.mark.asyncio
async def test_final_decision_ignores_pending_request(session: AsyncSession):
    req = await _make_request(session)

    out = await apply_final_decision(
        session,
        req,
        decided_by_user_id=MANAGING_PARTNER_ID,
        approve=True,
        decision_reason=None,
    )

    assert out.status == LEAVE_STATUS_PENDING
    assert out.final_decision_at is None


@pytest.mark.asyncio
async def test_partner_decision_ignores_pending_final_request(session: AsyncSession):
    req = await _make_request(session, status=LEAVE_STATUS_PENDING_FINAL)

    out = await apply_partner_decision(
        session,
        req,
        decided_by_user_id=PARTNER_ID,
        approve=True,
        decision_reason=None,
    )

    assert out.status == LEAVE_STATUS_PENDING_FINAL
    assert out.decision_at is None


@pytest.mark.asyncio
async def test_stale_pdf_is_regenerated_with_managing_partner(session: AsyncSession, tmp_path, monkeypatch):
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path))
    get_settings.cache_clear()
    try:
        req = await _make_request(session)
        req.pdf_storage_key = "vacation_leave_requests/2026/1/old.pdf"
        req.pdf_doc_version = 0
        await session.flush()

        assert await ensure_current_pdf(session, req) is True
        assert req.pdf_doc_version == LEAVE_PDF_DOC_VERSION
        assert req.pdf_storage_key is not None
        assert "old.pdf" not in req.pdf_storage_key
        # Повторный вызов ничего не пересобирает.
        assert await ensure_current_pdf(session, req) is False
    finally:
        get_settings.cache_clear()


def test_email_token_carries_stage():
    token = sign_email_action_token(
        SECRET,
        request_id=11,
        action="approve",
        ttl_seconds=60,
        stage=STAGE_FINAL,
    )

    assert verify_email_action_token(SECRET, token)["stg"] == STAGE_FINAL


def test_email_token_defaults_to_partner_stage():
    token = sign_email_action_token(SECRET, request_id=11, action="approve", ttl_seconds=60)

    assert verify_email_action_token(SECRET, token)["stg"] == STAGE_PARTNER
