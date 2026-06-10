import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from service_path import ensure_service_in_path

ensure_service_in_path("vacation")

from application.schedule_employee_sync import sync_schedule_employees_for_year
from infrastructure.models import ScheduleEmployee
from infrastructure.orm_base import Base


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_creates_rows_for_staff(session: AsyncSession):
    staff = [
        {"id": 10, "display_name": "Alina Abdullaeva", "email": "alina@example.com"},
        {"id": 11, "display_name": "Bob Smith", "email": "bob@example.com"},
    ]
    result = await sync_schedule_employees_for_year(session, year=2026, staff_users=staff)
    assert result.created == 2
    rows = (await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.year == 2026))).scalars().all()
    assert len(rows) == 2
    assert {r.auth_user_id for r in rows} == {10, 11}


@pytest.mark.asyncio
async def test_sync_links_orphan_excel_row_by_name(session: AsyncSession):
    session.add(
        ScheduleEmployee(
            year=2026,
            excel_row_no=1,
            auth_user_id=None,
            full_name="Alina Abdullaeva",
            email=None,
        )
    )
    await session.flush()
    staff = [{"id": 10, "display_name": "Alina Abdullaeva", "email": "alina@example.com"}]
    result = await sync_schedule_employees_for_year(session, year=2026, staff_users=staff)
    assert result.created == 0
    assert result.linked_orphans == 1
    row = (
        await session.execute(select(ScheduleEmployee).where(ScheduleEmployee.year == 2026))
    ).scalar_one()
    assert row.auth_user_id == 10
    assert row.email == "alina@example.com"


@pytest.mark.asyncio
async def test_sync_skips_hidden_admin(session: AsyncSession):
    staff = [{"id": 1, "display_name": "Главный администратор", "email": "admin@local"}]
    result = await sync_schedule_employees_for_year(session, year=2026, staff_users=staff)
    assert result.skipped_hidden == 1
    assert result.created == 0
