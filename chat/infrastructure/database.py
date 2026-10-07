from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from infrastructure.config import get_settings


def make_async_url(url: str) -> str:
    if not url or not url.strip():
        raise RuntimeError(
            "DATABASE_URL is not set. Set CHAT_DATABASE_URL in .env "
            "(e.g. postgresql://chat:chat@chat_db:5432/kosta_chat)."
        )
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url
    raise RuntimeError("DATABASE_URL must be postgresql:// or postgresql+asyncpg://")


engine = create_async_engine(make_async_url(get_settings().database_url), echo=False)

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
