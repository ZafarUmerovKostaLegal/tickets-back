from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from backend_common.cors_origins import resolve_cors_origins
from infrastructure.database import engine, Base
from sqlalchemy import text
from presentation.routes import health, ws_notifications, notifications_rest


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE notifications ADD COLUMN IF NOT EXISTS recipient_user_id INTEGER")
        )
        await conn.execute(
            text(
                "ALTER TABLE notifications ADD COLUMN IF NOT EXISTS "
                "notification_type VARCHAR(64) NOT NULL DEFAULT 'general'"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_notifications_recipient_user_id "
                "ON notifications (recipient_user_id)"
            )
        )
    yield


app = FastAPI(
    title="Notifications",
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
app.include_router(ws_notifications.router)
app.include_router(notifications_rest.router)
