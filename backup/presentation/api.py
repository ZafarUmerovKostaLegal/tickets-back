from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from application.scheduler import maybe_run_on_start, start_scheduler, stop_scheduler
from presentation.routes import backup_routes, health

BACKUP_API_PREFIX = "/api/v1/backup"


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    await maybe_run_on_start()
    yield
    stop_scheduler()


app = FastAPI(
    title="Backup",
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
app.include_router(health.router)
app.include_router(backup_routes.router)
