from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from infrastructure.config import get_settings


def make_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


_database_url = make_async_url(get_settings().database_url)
_pool_options = {} if _database_url.startswith("sqlite") else {"pool_size": 5, "max_overflow": 10, "pool_pre_ping": True, "pool_recycle": 1800}

engine = create_async_engine(_database_url, echo=False, **_pool_options)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,

    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session
