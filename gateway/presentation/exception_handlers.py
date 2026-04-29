

from __future__ import annotations

import logging
import traceback

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from infrastructure.config import get_settings

_log = logging.getLogger("gateway.errors")


def _request_id(request: Request) -> str:
    return getattr(getattr(request, "state", None), "request_id", None) or "—"


def register_exception_handlers(app) -> None:
    settings = get_settings()
    show_details = (settings.environment or "development").lower() in ("dev", "development", "local", "test")

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict):
            content = {**detail, "requestId": _request_id(request), "code": f"http_{exc.status_code}"}
        else:
            content = {
                "detail": detail,
                "code": f"http_{exc.status_code}",
                "requestId": _request_id(request),
            }
        return JSONResponse(status_code=exc.status_code, content=content)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "detail": "Validation error",
                "errors": exc.errors(),
                "code": "validation_error",
                "requestId": _request_id(request),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        _log.exception("unhandled: %s", exc)
        if show_details:
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                    "trace": traceback.format_exc()[-4000:],
                    "requestId": _request_id(request),
                    "code": "internal_error",
                },
            )
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal server error",
                "code": "internal_error",
                "requestId": _request_id(request),
            },
        )
