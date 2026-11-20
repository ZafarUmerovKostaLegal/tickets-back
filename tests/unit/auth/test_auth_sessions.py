

from application.session_policy import jtis_to_evict_after_register, session_jti_is_valid


def test_session_jti_is_valid_multi_device() -> None:
    assert session_jti_is_valid("a", ["a", "b"])
    assert session_jti_is_valid("b", ["a", "b"])
    assert not session_jti_is_valid("c", ["a", "b"])
    assert not session_jti_is_valid(None, ["a"])


def test_session_jti_is_valid_legacy_column() -> None:
    assert session_jti_is_valid("legacy", [], legacy_jti="legacy")
    assert not session_jti_is_valid("other", [], legacy_jti="legacy")


def test_session_jti_is_valid_no_sessions_legacy_tokens() -> None:
    assert session_jti_is_valid(None, [], legacy_jti=None)
    assert not session_jti_is_valid("x", [], legacy_jti=None)


def test_jtis_to_evict_after_register_fifo() -> None:
    assert jtis_to_evict_after_register(["a"], 2) == []
    assert jtis_to_evict_after_register(["a", "b"], 2) == ["a"]
    assert jtis_to_evict_after_register(["a", "b", "c"], 2) == ["a", "b"]


def test_no_device_limit_by_default() -> None:
    assert jtis_to_evict_after_register(["a", "b", "c", "d", "e"], 0) == []


def test_sessions_are_listed_described_and_ended() -> None:
    import asyncio
    import os
    from datetime import datetime, timedelta

    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from infrastructure.database import Base
    from infrastructure.models import UserAuthSessionModel
    from infrastructure.repositories import UserRepository

    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            repo = UserRepository(session)
            for index in range(5):  # five devices — none evicted
                await repo.register_session_jti(7, f"jti-{index}", max_sessions=0)
            await repo.describe_session("jti-4", user_agent="Mozilla/5.0 (iPhone) Safari", ip="10.0.0.5")
            await session.commit()
            rows = await repo.list_sessions(7)
            assert [row["jti"] for row in rows][0] == "jti-4" and len(rows) == 5
            assert rows[0]["userAgent"].startswith("Mozilla") and rows[0]["ip"] == "10.0.0.5"

            # Last activity is stamped at most every 5 minutes.
            stale = datetime.utcnow() - timedelta(hours=1)
            await session.execute(UserAuthSessionModel.__table__.update().values(last_seen_at=stale))
            await session.commit()
            await repo.touch_session("jti-1")
            seen = {row["jti"]: row["lastSeenAt"] for row in await repo.list_sessions(7)}
            assert seen["jti-1"] > seen["jti-2"]

            one = next(row for row in await repo.list_sessions(7) if row["jti"] == "jti-0")
            assert await repo.remove_session_by_id(7, one["id"]) is True
            assert await repo.remove_session_by_id(8, one["id"]) is False  # not someone else's
            assert await repo.remove_other_sessions(7, "jti-4") == 3
            assert await repo.list_active_session_jtis(7) == ["jti-4"]
        await engine.dispose()

    asyncio.run(run())
