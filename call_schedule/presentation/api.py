from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from presentation.routes import schedule_routes, day_files
from infrastructure.config import get_settings
from infrastructure.database import Base, engine
from infrastructure import models as _models  # noqa: F401


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Call schedule",
    version="0.2.0",
    lifespan=lifespan,
    description="Календари и события общего ящика (Microsoft Graph) + файлы на день.",
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

app.include_router(schedule_routes.router, prefix="/api/v1/call-schedule")
app.include_router(day_files.router, prefix="/api/v1/call-schedule")


@app.get("/health", tags=["health"])
async def health() -> dict:
    s = get_settings()
    tenant, client_id, client_secret = s.graph_client_credentials()
    graph_configured = bool(tenant and client_id and client_secret)
    return {
        "status": "ok",
        "service": "call_schedule",
        "mailbox_configured": bool((s.call_schedule_mailbox or "").strip()),
        "graph_configured": graph_configured,
    }
