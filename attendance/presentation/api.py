from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import engine, Base
from infrastructure.models import WorkdaySettingsModel
from presentation.routes import health, hikvision, settings


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
    yield


app = FastAPI(title="Kosta Attendance", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(health.router)
app.include_router(hikvision.router)
app.include_router(settings.router)
