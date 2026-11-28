import asyncio
import os
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")


def _run(fn):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from infrastructure.database import Base

    async def main():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await fn(session)
        await engine.dispose()

    asyncio.run(main())


def test_code_is_approved_once_and_then_spent():
    from application.outlook_pairing import OutlookPairingService, PairingNotFound

    async def flow(session):
        service = OutlookPairingService(session)
        started = await service.start("Outlook iOS")
        assert len(started.code) == 9 and started.code[4] == "-"
        assert await service.poll(started.pair_id, started.secret) is None

        info = await service.info(started.code.lower().replace("-", " "))
        assert (info.device, info.confirmed) == ("Outlook iOS", False)

        with pytest.raises(PairingNotFound):
            await service.poll(started.pair_id, "wrong-secret-value")

        await service.confirm(started.code, 7)
        with pytest.raises(PairingNotFound):
            await service.confirm(started.code, 8)
        assert await service.poll(started.pair_id, started.secret) == 7
        with pytest.raises(PairingNotFound):
            await service.poll(started.pair_id, started.secret)

    _run(flow)


def test_expired_code_cannot_be_used():
    from application.outlook_pairing import OutlookPairingService, PairingNotFound

    async def flow(session):
        clock = {"now": datetime(2026, 11, 28, 12, 0)}
        service = OutlookPairingService(session, now=lambda: clock["now"])
        started = await service.start()
        clock["now"] += timedelta(minutes=11)
        with pytest.raises(PairingNotFound):
            await service.confirm(started.code, 7)
        with pytest.raises(PairingNotFound):
            await service.poll(started.pair_id, started.secret)
        with pytest.raises(PairingNotFound):
            await service.info("ZZZZ")

    _run(flow)
