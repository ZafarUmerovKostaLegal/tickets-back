from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from infrastructure.database import Base, engine
from infrastructure import models as _models  # noqa: F401
from presentation.routes.board import router as board_router
from presentation.routes.tasks import router as tasks_router


def _ensure_sidebar_order_column(sync_conn) -> None:
    if sync_conn.dialect.name == "sqlite":
        rows = sync_conn.exec_driver_sql("PRAGMA table_info(legal_hub_project_folders)").fetchall()
        if rows and not any(row[1] == "sidebar_order" for row in rows):
            sync_conn.exec_driver_sql("ALTER TABLE legal_hub_project_folders ADD COLUMN sidebar_order JSON")
        return
    sync_conn.exec_driver_sql(
        "ALTER TABLE IF EXISTS legal_hub_project_folders ADD COLUMN IF NOT EXISTS sidebar_order JSON"
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_sidebar_order_column)
    yield


app = FastAPI(
    title="Legal Hub",
    version="0.1.0",
    lifespan=lifespan,
    description="Задачи команды: мои задачи, очередь проверки и общая загрузка.",
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
app.include_router(board_router, prefix="/api/v1/legal-hub")
app.include_router(tasks_router, prefix="/api/v1/legal-hub")


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "legal_hub"}
