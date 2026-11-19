from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend_common.cors_origins import resolve_cors_origins

from sqlalchemy import inspect, text

from infrastructure.database import Base, async_session_factory, engine
from infrastructure import models as _models  # noqa: F401
from presentation.routes.admin import router as admin_router
from presentation.routes.documents import router as documents_router
from presentation.routes.library import router as library_router
from presentation.routes.duplicates import router as duplicates_router
from presentation.routes.mail import router as mail_router
from presentation.routes.matters import router as matters_router
from presentation.routes.uploads import purge_stale_uploads, router as uploads_router

API_PREFIX = "/api/v1/files"


async def _ensure_schema(conn) -> None:
    """Columns added after the first release (create_all does not alter existing tables)."""
    columns = await conn.run_sync(lambda sync: {col["name"] for col in inspect(sync).get_columns("fm_versions")})
    if "blob_sha" not in columns:
        await conn.execute(text("ALTER TABLE fm_versions ADD COLUMN blob_sha VARCHAR(64)"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fm_versions_blob_sha ON fm_versions (blob_sha)"))
    if conn.dialect.name == "postgresql":
        # Files over 2 GB: sizes no longer fit into INTEGER.
        for table in ("fm_documents", "fm_versions"):
            await conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN size_bytes TYPE BIGINT"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_schema(conn)
    async with async_session_factory() as session:
        await purge_stale_uploads(session)
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
app.include_router(mail_router, prefix=API_PREFIX)
app.include_router(duplicates_router, prefix=API_PREFIX)
app.include_router(uploads_router, prefix=API_PREFIX)


@app.get("/health", tags=["health"])
async def health() -> dict:
    return {"status": "ok", "service": "file_manager"}
