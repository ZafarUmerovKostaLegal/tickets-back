from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend_common.sql_injection_guard import SqlInjectionGuardMiddleware
from backend_common.rate_limit import RateLimitMiddleware
from backend_common.cors_origins import resolve_cors_origins
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
    ops_databases,
    auth_azure,
    users,
    positions,
    tickets,
    notifications,
    notifications_rest,
    inventory_routes,
    roles,
    todos_routes,
    chat_routes,
    contacts_routes,
    correspondence_routes,
    chat_ws,
    call_schedule_routes,
    smart_home_routes,
    media,
    attendance_routes,
    vacation_routes,
    time_tracking_routes,
    time_tracking_users_hourly_alias,
    expenses_routes,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from infrastructure.attendance_range_snapshot import start_snapshot_scheduler, stop_snapshot_scheduler

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
    await start_snapshot_scheduler()
    try:
        yield
    finally:
        await stop_snapshot_scheduler()


app = FastAPI(
    title="Gateway",
    version="1.0.0",
    lifespan=_lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
register_exception_handlers(app)


def _cors_origins() -> list[str]:
    return resolve_cors_origins()


def _cors_origin_regex(settings) -> str | None:
    """Kept for tests; wildcard origins do not need a regex."""
    _ = settings
    return None


app.add_middleware(TimeTrackingClientsPathRewriteMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Location"],
)
app.add_middleware(SqlInjectionGuardMiddleware)
app.add_middleware(IncomingAuthorizationMiddleware)

app.add_middleware(RequestIdMiddleware)
app.add_middleware(RateLimitMiddleware, service_name="gateway")
app.include_router(spa_auth_callback.router)
app.include_router(ops.router)
app.include_router(ops_databases.router)
app.include_router(health.router)
app.include_router(desktop_backgrounds_public.router)
app.include_router(auth_azure.router)
app.include_router(users.router)
app.include_router(positions.router)
app.include_router(time_tracking_users_hourly_alias.router)
app.include_router(tickets.router)
app.include_router(notifications.router)
app.include_router(notifications_rest.router)
app.include_router(inventory_routes.router)
app.include_router(roles.router)
app.include_router(todos_routes.router)
app.include_router(chat_ws.router)
app.include_router(chat_routes.router)
app.include_router(contacts_routes.router)
app.include_router(correspondence_routes.router)
app.include_router(call_schedule_routes.router)
app.include_router(smart_home_routes.router)
app.include_router(media.router)
app.include_router(attendance_routes.router_compat)
app.include_router(attendance_routes.router)
app.include_router(vacation_routes.router)
app.include_router(time_tracking_routes.router)
app.include_router(expenses_routes.router)
