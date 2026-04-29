

from __future__ import annotations

from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from infrastructure.config import get_settings

_incoming_authorization: ContextVar[str | None] = ContextVar("incoming_authorization", default=None)


class IncomingAuthorizationMiddleware(BaseHTTPMiddleware):


    async def dispatch(self, request: Request, call_next):
        auth = request.headers.get("Authorization")
        if not (auth or "").strip():
            name = (get_settings().auth_session_cookie_name or "").strip()
            if name:
                tok = (request.cookies.get(name) or "").strip()
                if tok:
                    auth = f"Bearer {tok}"
        token = _incoming_authorization.set(auth)
        try:
            return await call_next(request)
        finally:
            _incoming_authorization.reset(token)


def get_incoming_authorization() -> str | None:
    return _incoming_authorization.get()


def merge_upstream_headers(headers: dict[str, str] | None = None) -> dict[str, str] | None:

    merged = dict(headers or {})
    auth = get_incoming_authorization()
    if auth:
        merged["Authorization"] = auth
    return merged if merged else None
