from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from infrastructure.database import Base, engine
from infrastructure import models as _models  # noqa: F401
from presentation.routes.accounting_settings import router as accounting_settings_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
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


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "hr"}
