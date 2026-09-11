from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from backend_common.cors_origins import resolve_cors_origins

from infrastructure.database import Base, engine, async_session_factory
from infrastructure.models import InternalExtensionModel  # noqa: F401
from infrastructure.seed_internal_extensions import DEFAULT_INTERNAL_EXTENSIONS
from presentation.routes import client_contacts, colleagues, health, internal_extensions

CONTACTS_API_PREFIX = "/api/v1/contacts"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_session_factory() as session:
        count = await session.scalar(select(func.count()).select_from(InternalExtensionModel))
        if not count:
            for full_name, extension in DEFAULT_INTERNAL_EXTENSIONS:
                session.add(InternalExtensionModel(full_name=full_name, extension=extension))
            await session.commit()
    yield


app = FastAPI(
    title="Contacts",
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
app.include_router(colleagues.router, prefix=CONTACTS_API_PREFIX)
app.include_router(client_contacts.router, prefix=CONTACTS_API_PREFIX)
app.include_router(internal_extensions.router, prefix=CONTACTS_API_PREFIX)
