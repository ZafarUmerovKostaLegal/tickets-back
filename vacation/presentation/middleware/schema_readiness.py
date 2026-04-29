

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from infrastructure.schema_readiness import schema_ready_event

_SKIP_PATHS = frozenset(
    {
        "/health",
        "/openapi.json",
        "/docs",
        "/redoc",
    }
)


class SchemaReadinessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        p = request.url.path
        if p in _SKIP_PATHS or p.startswith("/docs/"):
            return await call_next(request)
        if not schema_ready_event.is_set():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Схема БД ещё инициализируется. Повторите запрос через несколько секунд.",
                },
            )
        return await call_next(request)
