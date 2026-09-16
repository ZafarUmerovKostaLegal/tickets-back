from contextlib import asynccontextmanager
from datetime import date
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from backend_common.cors_origins import resolve_cors_origins
from infrastructure.config import get_settings
from infrastructure.database import engine, Base
from infrastructure import models  # noqa: F401 — register ORM tables
from infrastructure.ingest_poller import start_backfill_background, start_ingest_poller, stop_ingest_poller
from presentation.routes import health, hikvision, ingest, settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        await conn.execute(
            text(
                "ALTER TABLE IF EXISTS attendance_explanations "
                "ADD COLUMN IF NOT EXISTS explanation_file_path VARCHAR(1024)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE IF EXISTS attendance_explanations "
                "ALTER COLUMN explanation_text DROP NOT NULL"
            )
        )
        # Safety net if create_all raced an older schema without the unique index.
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_attendance_camera_event "
                "ON attendance_camera_events (camera_ip, person_id, event_time, checkpoint)"
            )
        )

    await start_ingest_poller()

    cfg = get_settings()
    if (cfg.attendance_backfill_from or "").strip() and (cfg.attendance_backfill_to or "").strip():
        try:
            start = date.fromisoformat(cfg.attendance_backfill_from.strip()[:10])
            end = date.fromisoformat(cfg.attendance_backfill_to.strip()[:10])
            today = date.today()
            if end > today:
                end = today
            if end >= start:
                logger.info("Starting configured camera backfill %s .. %s", start, end)
                start_backfill_background(start, end)
        except Exception:
            logger.exception("Failed to start configured attendance backfill")

    yield
    await stop_ingest_poller()


app = FastAPI(
    title="Kosta Attendance",
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
app.include_router(hikvision.router)
app.include_router(ingest.router)
app.include_router(settings.router)
