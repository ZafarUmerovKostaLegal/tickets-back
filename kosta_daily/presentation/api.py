import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.schema_patch_runner import apply_registered_schema_patches
from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from backend_common.cors_origins import resolve_cors_origins
from infrastructure.database import Base, connect_with_retry, engine, is_db_disabled
from infrastructure.schema_patches import REGISTERED_CHAT_SCHEMA_PATCHES
from presentation.routes import (
    attachments_routes,
    checklists_routes,
    health,
    messages_routes,
    pins_routes,
    polls_routes,
    push_routes,
    retention_routes,
    rooms_routes,
)

KOSTA_DAILY_API_PREFIX = "/api/v1/kosta-daily"
_log = logging.getLogger("kosta_daily.startup")


async def _prepare_database() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await apply_registered_schema_patches(
            conn,
            REGISTERED_CHAT_SCHEMA_PATCHES,
            table_name="chat_schema_patch_log",
            log_prefix="kosta_daily",
        )


async def _prepare_database_forever() -> None:
    if is_db_disabled():
        _log.warning("CHAT_DISABLE_DB=1 — database init skipped; HTTP only")
        return
    delay = 2.0
    while True:
        try:
            await connect_with_retry(_prepare_database, attempts=10)
            _log.info("kosta_daily database is ready")
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error(
                "kosta_daily database init failed; HTTP stays up, retrying in %.0fs: %s",
                delay,
                exc,
            )
            try:
                await engine.dispose()
            except Exception:
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 30.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_prepare_database_forever(), name="kosta-daily-db-init")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="Kosta Daily",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=resolve_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(health.router)
app.include_router(rooms_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
app.include_router(messages_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
app.include_router(polls_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
app.include_router(pins_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
app.include_router(checklists_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
app.include_router(push_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
app.include_router(attachments_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
app.include_router(retention_routes.router, prefix=KOSTA_DAILY_API_PREFIX)
