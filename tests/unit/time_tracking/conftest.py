from support.service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")


from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from support.tt_invoice import TT_NOW  # noqa: E402


@pytest.fixture()
async def tt_db(tmp_path):
    """Empty time-tracking database with client c1 and project p1 (USD, no partner gate)."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    import infrastructure.models  # noqa: F401
    import infrastructure.models_invoices  # noqa: F401
    import infrastructure.models_reports  # noqa: F401
    from infrastructure.database import Base
    from infrastructure.models import TimeManagerClientModel, TimeManagerClientProjectModel

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'tt.db').as_posix()}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as s:
            s.add(TimeManagerClientModel(id="c1", name="Acme", currency="USD", created_at=TT_NOW))
            s.add(TimeManagerClientProjectModel(
                id="p1", client_id="c1", name="Falcon", project_type="time_and_materials", currency="USD",
                skip_partner_invoice_confirmation=True, project_billable_rate_amount=Decimal("100"), created_at=TT_NOW,
            ))
            await s.commit()
            yield s
    finally:
        await engine.dispose()
