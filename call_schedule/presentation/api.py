from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from presentation.routes import schedule_routes
from infrastructure.config import get_settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(
    title="Call schedule",
    version="0.1.0",
    lifespan=lifespan,
    description="Календари и события общего ящика (Microsoft Graph, без БД).",
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