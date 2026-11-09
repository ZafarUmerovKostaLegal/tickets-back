from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from infrastructure.database import Base, engine
from infrastructure import models as _models  # noqa: F401
from presentation.routes.admin import router as admin_router
from presentation.routes.documents import router as documents_router
from presentation.routes.library import router as library_router
from presentation.routes.matters import router as matters_router

API_PREFIX = "/api/v1/files"


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="File Manager",
    version="0.1.0",
    lifespan=lifespan,
    description="Matter-centric document management: clients, matters, versions, access control, audit.",
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
app.include_router(matters_router, prefix=API_PREFIX)
app.include_router(documents_router, prefix=API_PREFIX)
app.include_router(library_router, prefix=API_PREFIX)
app.include_router(admin_router, prefix=API_PREFIX)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "file_manager"}
