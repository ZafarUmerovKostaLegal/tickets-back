from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.schema_patch_runner import apply_registered_schema_patches
from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import Base, engine
from infrastructure.schema_patches import REGISTERED_CHAT_SCHEMA_PATCHES
from presentation.routes import attachments_routes, health, messages_routes, polls_routes, retention_routes, rooms_routes

CHAT_API_PREFIX = "/api/v1/chat"


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await apply_registered_schema_patches(
            conn,
            REGISTERED_CHAT_SCHEMA_PATCHES,
            table_name="chat_schema_patch_log",
            log_prefix="chat",
        )
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
app.include_router(polls_routes.router, prefix=CHAT_API_PREFIX)
app.include_router(attachments_routes.router, prefix=CHAT_API_PREFIX)
app.include_router(retention_routes.router, prefix=CHAT_API_PREFIX)
