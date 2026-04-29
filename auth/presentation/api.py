import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.database import engine, Base
from infrastructure.config import get_settings, validate_production_secrets
from presentation.routes import auth_routes, user_routes, role_routes, health
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


app = FastAPI(title="Auth", version="1.0.0", lifespan=lifespan)
def _auth_cors_origins() -> list[str]:
    s = get_settings()
    origins: list[str] = []
    for url in (s.frontend_url or "").strip(), (s.admin_frontend_url or "").strip():
        if url and url != "*":
            origins.extend(u.strip() for u in url.split(",") if u.strip() and u.strip() != "*")
    if not origins:
        origins = [
            "http://localhost:5173",
            "http://localhost:8080",
            "http://127.0.0.1:8080",
            "http://localhost:8081",
            "http://127.0.0.1:8081",
            "http://localhost:5500",
            "http://127.0.0.1:5500",
        ]
    return list(dict.fromkeys(origins))


app.add_middleware(
    CORSMiddleware,
    allow_origins=_auth_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.include_router(auth_routes.router)
app.include_router(user_routes.router)
app.include_router(role_routes.router)
app.include_router(health.router)
