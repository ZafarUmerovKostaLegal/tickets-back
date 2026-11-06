from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from infrastructure.database import Base, engine
from infrastructure import models as _models  # noqa: F401
from presentation.routes.board import router as board_router
from presentation.routes.tasks import router as tasks_router


def _ensure_task_columns(sync_conn) -> None:
    boolean = "0" if sync_conn.dialect.name == "sqlite" else "FALSE"
    columns = {
        "project_id": "VARCHAR(80) DEFAULT ''",
        "priority": "VARCHAR(20) DEFAULT 'normal'",
        "internal_on": "DATE",
        "review_on": "DATE",
        "instructions": "TEXT DEFAULT ''",
        "approval": f"BOOLEAN DEFAULT {boolean}",
        "board_column": "VARCHAR(40) DEFAULT ''",
        "important": f"BOOLEAN DEFAULT {boolean}",
        "assignee_name": "VARCHAR(200) DEFAULT ''",
        "assignee_code": "VARCHAR(16) DEFAULT ''",
    }
    if sync_conn.dialect.name == "sqlite":
        rows = sync_conn.exec_driver_sql("PRAGMA table_info(legal_hub_tasks)").fetchall()
        present = {row[1] for row in rows}
        if not rows:
            return
        for name, ddl in columns.items():
            if name not in present:
                sync_conn.exec_driver_sql(f"ALTER TABLE legal_hub_tasks ADD COLUMN {name} {ddl}")
        return
    for name, ddl in columns.items():
        sync_conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS legal_hub_tasks ADD COLUMN IF NOT EXISTS {name} {ddl}"
        )


def _ensure_folder_columns(sync_conn) -> None:
    columns = ("sidebar_order", "archived_clients")
    if sync_conn.dialect.name == "sqlite":
        rows = sync_conn.exec_driver_sql("PRAGMA table_info(legal_hub_project_folders)").fetchall()
        present = {row[1] for row in rows}
        if not rows:
            return
        for name in columns:
            if name not in present:
                sync_conn.exec_driver_sql(f"ALTER TABLE legal_hub_project_folders ADD COLUMN {name} JSON")
        return
    for name in columns:
        sync_conn.exec_driver_sql(
            f"ALTER TABLE IF EXISTS legal_hub_project_folders ADD COLUMN IF NOT EXISTS {name} JSON"
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_task_columns)
        await conn.run_sync(_ensure_folder_columns)
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
