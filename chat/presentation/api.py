from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import Base, engine
from presentation.routes import attachments_routes, health, messages_routes, rooms_routes

CHAT_API_PREFIX = "/api/v1/chat"


async def _ensure_reply_column(conn) -> None:
    await conn.execute(
        text(
            """
            ALTER TABLE chat_messages
            ADD COLUMN IF NOT EXISTS reply_to_message_id BIGINT NULL
            REFERENCES chat_messages(id) ON DELETE SET NULL
            """
        )
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_reply_column(conn)
    yield


app = FastAPI(
    title="Chat",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(health.router)
app.include_router(rooms_routes.router, prefix=CHAT_API_PREFIX)
app.include_router(messages_routes.router, prefix=CHAT_API_PREFIX)
app.include_router(attachments_routes.router, prefix=CHAT_API_PREFIX)
