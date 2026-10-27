from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from infrastructure.database import Base, engine
from infrastructure import models as _models  # noqa: F401
from presentation.routes.accounting_settings import router as accounting_settings_router
from presentation.routes.personnel import router as personnel_router


def _adopt_legacy_media() -> None:
    """Keep photos that were written to the old container path before the data volume."""
    from pathlib import Path

    from infrastructure.config import get_settings
    from infrastructure.personnel_files import save_personnel_bytes
    from backend_common.media_path import safe_media_path

    current = Path(get_settings().media_path).resolve()
    legacy = Path("/app/media")
    try:
        if not legacy.is_dir() or legacy.resolve() == current:
            return
        source = legacy / "hr_personnel"
        if not source.is_dir():
            return
        for src in source.rglob("*"):
            if not src.is_file():
                continue
            relative = src.relative_to(legacy).as_posix()
            dest = safe_media_path(current, relative)
            if dest is None or dest.is_file():
                continue
            save_personnel_bytes(current, relative, src.read_bytes())
    except OSError:
        return


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _adopt_legacy_media()
    yield


app = FastAPI(
    title="HR",
    version="0.1.0",
    lifespan=lifespan,
    description="HR и настройки бухгалтерии.",
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
app.include_router(accounting_settings_router, prefix="/api/v1/hr")
app.include_router(personnel_router, prefix="/api/v1/hr")


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "hr"}
