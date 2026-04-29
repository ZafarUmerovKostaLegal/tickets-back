import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.config import get_settings, resolve_database_url
from infrastructure.orm_base import Base
from infrastructure import models

_log = logging.getLogger("vacation.db")


def make_async_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _engine():
    settings = get_settings()
    url = resolve_database_url(settings).strip()
    if not url:
        _log.warning("URL БД пуст: задайте VACATION_DB_* (или явный DATABASE_URL при VACATION_USE_EXPLICIT_DATABASE_URL=true).")
        return None
    if settings.vacation_use_explicit_database_url and (settings.database_url or "").strip():
        _log.info("Подключение к БД: явный DATABASE_URL/VACATION_DATABASE_URL (host в URL).")
    else:
        _log.info(
            "Подключение к БД из частей: %s@%s:%s/%s",
            settings.vacation_db_user,
            settings.vacation_db_host,
            settings.vacation_db_port,
            settings.vacation_db_name,
        )
    return create_async_engine(make_async_url(url), echo=False)


engine = _engine()

async_session_factory = (
    async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False, autocommit=False, autoflush=False)
    if engine is not None
    else None
)


async def get_session() -> AsyncSession:
    if async_session_factory is None:
        raise RuntimeError("DATABASE_URL is not configured")
    async with async_session_factory() as session:
        yield session
