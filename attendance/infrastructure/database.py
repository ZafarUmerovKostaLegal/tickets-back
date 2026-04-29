from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from infrastructure.config import get_settings


def make_async_url(url: str) -> str:
    if not url or not url.strip():
        raise RuntimeError(
            "DATABASE_URL is not set. Set ATTENDANCE_DATABASE_URL in .env "
            "(e.g. ATTENDANCE_DATABASE_URL=postgresql://attendance:password@attendance_db:5432/kosta_attendance)."
        )
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url
    raise RuntimeError("DATABASE_URL must be postgresql:// or postgresql+asyncpg://")


def _get_engine():
    settings = get_settings()
    return create_async_engine(
        make_async_url(settings.database_url),
        echo=False,
    )


engine = _get_engine()

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
