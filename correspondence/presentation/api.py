import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from backend_common.cors_origins import resolve_cors_origins
from infrastructure.database import Base, engine
from infrastructure import models              
from presentation.routes import correspondence, health

_log = logging.getLogger("correspondence.startup")

_STARTUP_RETRIES = 30
_STARTUP_DELAY_SEC = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    last_exc: Exception | None = None
    for attempt in range(1, _STARTUP_RETRIES + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                from sqlalchemy import text

                await conn.execute(
                    text(
                        "ALTER TABLE correspondence_documents "
                        "ALTER COLUMN registry_number DROP NOT NULL"
                    )
                )
                await conn.execute(
                    text(
                        "ALTER TABLE correspondence_documents "
                        "ALTER COLUMN registered_at DROP NOT NULL"
                    )
                )
                await conn.execute(
                    text(
                        "ALTER TABLE correspondence_documents "
                        "ADD COLUMN IF NOT EXISTS rejection_comment TEXT"
                    )
                )
                await conn.execute(
                    text(
                        """
                        CREATE TABLE IF NOT EXISTS correspondence_document_comments (
                            id VARCHAR(36) PRIMARY KEY,
                            document_id VARCHAR(36) NOT NULL
                                REFERENCES correspondence_documents (id) ON DELETE CASCADE,
                            author_user_id INTEGER NOT NULL,
                            body TEXT NOT NULL,
                            created_at TIMESTAMPTZ NOT NULL
                        )
                        """
                    )
                )
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_corr_doc_comments_document_id "
                        "ON correspondence_document_comments (document_id)"
                    )
                )
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_corr_doc_comments_document_created "
                        "ON correspondence_document_comments (document_id, created_at)"
                    )
                )
            break
        except Exception as e:
            last_exc = e
            _log.warning(
                "БД недоступна для инициализации (попытка %s/%s): %s",
                attempt,
                _STARTUP_RETRIES,
                e,
            )
            await asyncio.sleep(_STARTUP_DELAY_SEC)
    else:
        assert last_exc is not None
        raise last_exc
    yield


app = FastAPI(
    title="Kosta Correspondence",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    redirect_slashes=False,
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
app.include_router(correspondence.router, prefix="/api/v1")
