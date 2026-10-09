from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, AsyncIterator

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from infrastructure.config import get_settings

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

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_db_ready: bool = False


class Base(DeclarativeBase):
    pass


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


def is_db_disabled() -> bool:
    return bool(get_settings().chat_disable_db)


def is_db_ready() -> bool:
    return _db_ready and _engine is not None and not is_db_disabled()


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if is_db_disabled():
        raise RuntimeError("Chat database is disabled (CHAT_DISABLE_DB=1)")
    if _engine is None:
        url = make_async_url(get_settings().database_url)
        _engine = create_async_engine(url, echo=False)
        _session_factory = async_sessionmaker(
            _engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _engine


def _factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


class _EngineProxy:
    def begin(self):
        return get_engine().begin()

    async def dispose(self) -> None:
        global _engine, _session_factory, _db_ready
        if _engine is not None:
            await _engine.dispose()
            _engine = None
            _session_factory = None
            _db_ready = False


engine = _EngineProxy()


async def get_session() -> AsyncIterator[AsyncSession]:
    if is_db_disabled():
        raise HTTPException(status_code=503, detail="Chat database temporarily disabled")
    if not is_db_ready():
        raise HTTPException(status_code=503, detail="Chat database is not ready yet")
    async with _factory()() as session:
        yield session


def is_transient_database_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


async def connect_with_retry(prepare: Callable[[], Awaitable[None]], *, attempts: int = 30) -> None:
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            get_engine()
            await prepare()
            global _db_ready
            _db_ready = True
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
