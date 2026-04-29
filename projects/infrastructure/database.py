from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from infrastructure.config import get_settings


def make_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _engine():
    settings = get_settings()
    url = (settings.database_url or "").strip()
    if not url:
        return None
    return create_async_engine(make_async_url(url), echo=False)


engine = _engine()

async_session_factory = (
    async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False)
    if engine is not None
    else None
)


class Base(DeclarativeBase):
    pass


async def get_session() -> AsyncSession:
    if async_session_factory is None:
        raise RuntimeError("DATABASE_URL is not configured")
    async with async_session_factory() as session:
        yield session
