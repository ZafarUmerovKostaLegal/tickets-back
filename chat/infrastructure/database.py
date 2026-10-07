import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from infrastructure.config import get_settings, resolve_database_url

_log = logging.getLogger("chat.database")

_TRANSIENT_MARKERS = (
    "the database system is starting up",
    "the database system is shutting down",
    "connection refused",
    "temporary failure in name resolution",
    "name or service not known",
    "could not translate host name",
    "timeout expired",
    "connection timed out",
)


def make_async_url(url: str) -> str:
    if not url or not url.strip():
        raise RuntimeError(
            "DATABASE_URL is not set. Set CHAT_DATABASE_URL or CHAT_DB_PASSWORD in .env "
            "(e.g. postgresql://chat:chat@chat_db:5432/kosta_chat)."
        )
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgresql+asyncpg://"):
        return url
    raise RuntimeError("DATABASE_URL must be postgresql:// or postgresql+asyncpg://")


engine = create_async_engine(make_async_url(resolve_database_url(get_settings())), echo=False)

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


def is_transient_database_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


async def connect_with_retry(prepare: Callable[[], Awaitable[None]], *, attempts: int = 30) -> None:
    """Keep the process alive while Postgres is still starting.

    A single failed connection makes uvicorn exit, Docker restarts the container
    immediately, and the crash loop hides the Restart button.
    """
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            await prepare()
            return
        except Exception as exc:
            if not is_transient_database_error(exc) or attempt == attempts:
                raise
            _log.warning(
                "chat database is not ready (attempt %s/%s): %s",
                attempt,
                attempts,
                exc,
            )
            await engine.dispose()
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 5.0)
