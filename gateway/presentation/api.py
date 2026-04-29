from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from infrastructure.upstream_auth_context import IncomingAuthorizationMiddleware
from infrastructure.config import get_settings
from presentation.exception_handlers import register_exception_handlers
from presentation.middleware.request_id import RequestIdMiddleware
from presentation.middleware.security_headers import SecurityHeadersMiddleware
from presentation.middleware.time_tracking_clients_rewrite import TimeTrackingClientsPathRewriteMiddleware
from presentation.routes import (
    desktop_backgrounds_public,
    spa_auth_callback,
    health,
    ops,
    auth_azure,
    auth_admin,
    users,
    tickets,
    notifications,
    notifications_rest,
    inventory_routes,
    roles,
    todos_routes,
    call_schedule_routes,
    media,
    attendance_routes,
    vacation_routes,
    time_tracking_routes,
    time_tracking_users_hourly_alias,
    expenses_routes,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    s = get_settings()
    dsn = (s.sentry_dsn or "").strip()
    if dsn:
        try:
            import sentry_sdk

            sentry_sdk.init(
                dsn=dsn,
                environment=(s.environment or "development").strip(),
                traces_sample_rate=0.1,
            )
        except Exception:
            pass
    yield


app = FastAPI(title="Gateway", version="1.0.0", lifespan=_lifespan)
register_exception_handlers(app)

def _cors_origins() -> list[str]:
    settings = get_settings()
    origins: list[str] = []
    for url in (settings.frontend_url or "").strip(), (settings.admin_frontend_url or "").strip():
        if url and url != "*":
            origins.extend(u.strip() for u in url.split(",") if u.strip() and u.strip() != "*")
    defaults = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8080",
        "http://127.0.0.1:8080",

        "http://localhost:8081",
        "http://127.0.0.1:8081",

        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ]
    for o in defaults:
        if o not in origins:
            origins.append(o)
    if not origins:
        origins = defaults + ["null"]
    return list(dict.fromkeys(origins))


_CORS_PRIVATE_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2[0-9]|3[0-1])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)

origins = _cors_origins()
_settings = get_settings()
_cors_regex = _CORS_PRIVATE_ORIGIN_REGEX if _settings.cors_allow_private_network else None


app.add_middleware(TimeTrackingClientsPathRewriteMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=_cors_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Location"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.add_middleware(IncomingAuthorizationMiddleware)

app.add_middleware(RequestIdMiddleware)
app.include_router(spa_auth_callback.router)
app.include_router(ops.router)
app.include_router(health.router)
app.include_router(desktop_backgrounds_public.router)
app.include_router(auth_azure.router)
app.include_router(auth_admin.router)
app.include_router(users.router)
app.include_router(time_tracking_users_hourly_alias.router)
app.include_router(tickets.router)
app.include_router(notifications.router)
app.include_router(notifications_rest.router)
app.include_router(inventory_routes.router)
app.include_router(roles.router)
app.include_router(todos_routes.router)
app.include_router(call_schedule_routes.router)
app.include_router(media.router)
app.include_router(attendance_routes.router_compat)
app.include_router(attendance_routes.router)
app.include_router(vacation_routes.router)
app.include_router(time_tracking_routes.router)
app.include_router(expenses_routes.router)
