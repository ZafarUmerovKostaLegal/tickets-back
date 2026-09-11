from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from infrastructure.config import get_settings


def make_async_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        raw = "sqlite+aiosqlite:///./data/contacts.db"
    if raw.startswith("sqlite://") and not raw.startswith("sqlite+aiosqlite://"):
        return raw.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def _ensure_sqlite_dir(url: str) -> None:
    if "sqlite" not in url:
        return
    prefix = "sqlite+aiosqlite:///"
    if not url.startswith(prefix):
        return
    path_part = url[len(prefix) :]
    if path_part.startswith("/") and not path_part.startswith("//"):
        db_path = Path(path_part)
    else:
        db_path = Path(path_part)
    if db_path.parent and str(db_path.parent) not in {"", "."}:
        db_path.parent.mkdir(parents=True, exist_ok=True)


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_async_url = make_async_url(_settings.database_url)
_ensure_sqlite_dir(_async_url)

engine_kwargs: dict = {"echo": False}
if ":memory:" in _async_url:
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    engine_kwargs["poolclass"] = StaticPool

engine = create_async_engine(_async_url, **engine_kwargs)
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session
