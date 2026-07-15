import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from backend_common.cors_origins import resolve_cors_origins
from infrastructure.database import engine, Base
from infrastructure.config import get_settings, validate_production_secrets
from presentation.routes import auth_routes, user_routes, role_routes, health, internal_routes
from presentation.startup import ensure_auth_schema, seed_default_roles

_log = logging.getLogger("auth.startup")


_STARTUP_RETRIES = 30
_STARTUP_DELAY_SEC = 2.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_production_secrets(get_settings())
    last_exc: Exception | None = None
    for attempt in range(1, _STARTUP_RETRIES + 1):
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await ensure_auth_schema(conn)
                await conn.run_sync(seed_default_roles)
            break
        except Exception as e:
            last_exc = e
            _log.warning(
                "БД недоступна для инициализации auth (попытка %s/%s): %s",
                attempt,
                _STARTUP_RETRIES,
                e,
            )
            await asyncio.sleep(_STARTUP_DELAY_SEC)
    else:
        assert last_exc is not None
        _log.error("Не удалось инициализировать auth после %s попыток", _STARTUP_RETRIES)
        raise last_exc
    yield


app = FastAPI(
    title="Auth",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


def _auth_cors_origins() -> list[str]:
    s = get_settings()
    return resolve_cors_origins(
        frontend_url=s.frontend_url,
        environment=getattr(s, "environment", None) or "",
        include_local_defaults=True,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=_auth_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
# Rate limiting stays on gateway only — auth is called by every service via /users/me
# from a few Docker IPs; per-IP limits there lock out the whole cluster.
app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(role_routes.router)
app.include_router(health.router)
app.include_router(internal_routes.router)
