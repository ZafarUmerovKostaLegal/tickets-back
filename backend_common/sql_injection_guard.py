

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_ENABLED = os.getenv("SQL_INJECTION_GUARD_ENABLED", "true").lower() in ("1", "true", "yes")
_CHECK_BODY = os.getenv("SQL_INJECTION_GUARD_CHECK_BODY", "true").lower() in ("1", "true", "yes")
_MAX_BODY = int(os.getenv("SQL_INJECTION_GUARD_MAX_BODY_BYTES", "262144"))


_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?is)\bunion\s+all\s+select\b"),
    re.compile(r"(?is)\bunion\s+select\b"),
    re.compile(r"(?is)--[\s\r\n]"),
    re.compile(r"(?is)/\*.*\*/"),
    re.compile(r"(?is);\s*(drop|delete|truncate|insert|update|alter|create\s+table|exec|execute)\b"),
    re.compile(r"(?is)\bor\b\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+"),
    re.compile(r"(?is)\band\b\s+['\"]?\d+['\"]?\s*=\s*['\"]?\d+"),
    re.compile(r"(?is)(?:^|[^\d])1\s*=\s*1(?:[^\d]|$)"),
    re.compile(r"(?is)\b1\s*=\s*'1'\b"),
    re.compile(r"(?is)'--"),
    re.compile(r"(?is)--\s*$"),
    re.compile(r"(?is)information_schema\b"),
    re.compile(r"(?is)\bsys\.databases\b"),
    re.compile(r"(?is)\bsys\.tables\b"),
    re.compile(r"(?is)\bsleep\s*\("),
    re.compile(r"(?is)\bpg_sleep\b"),
    re.compile(r"(?is)\bwaitfor\b\s+\b(delay|time)\b"),
    re.compile(r"(?is)\bbenchmark\s*\("),
    re.compile(r"(?is)@@version\b"),
    re.compile(r"(?is)\bxp_cmdshell\b"),
    re.compile(r"(?is)\boutfile\b"),
    re.compile(r"(?is)\bload_file\s*\("),
    re.compile(r"(?is)\binto\s+outfile\b"),
    re.compile(r"(?is)\bchr\s*\(\s*\d"),
    re.compile(r"(?is)\bconcat\s*\(\s*0x"),
)


def contains_sql_injection_pattern(value: str) -> bool:

    if not value or len(value) > 200_000:
        return False
    s = value.strip()
    if len(s) < 3:
        return False
    low = s.lower()
    for p in _PATTERNS:
        if p.search(low):
            return True
    return False


def _scan_json_value(obj: Any, *, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if isinstance(obj, str):
        return contains_sql_injection_pattern(obj)
    if isinstance(obj, list):
        return any(_scan_json_value(x, depth=depth + 1) for x in obj[:5000])
    if isinstance(obj, dict):
        for i, (k, v) in enumerate(obj.items()):
            if i >= 5000:
                break
            if isinstance(k, str) and contains_sql_injection_pattern(k):
                return True
            if _scan_json_value(v, depth=depth + 1):
                return True
    return False


def _bad_request() -> JSONResponse:

    return JSONResponse(
        status_code=400,
        content={"detail": "Некорректный запрос"},
    )


class SqlInjectionGuardMiddleware(BaseHTTPMiddleware):


    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        if not _ENABLED:
            return await call_next(request)
        if request.scope.get("type") != "http":
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)

        raw_path = request.url.path or ""

        if raw_path == "/api/v1/auth/azure/callback":
            return await call_next(request)
        try:
            path_decoded = unquote(raw_path)
        except Exception:
            path_decoded = raw_path
        if contains_sql_injection_pattern(path_decoded):
            return _bad_request()

        for key, val in request.query_params.multi_items():
            try:
                k = unquote(str(key))
                v = unquote(str(val))
            except Exception:
                k, v = str(key), str(val)
            if contains_sql_injection_pattern(k) or contains_sql_injection_pattern(v):
                return _bad_request()

        if (
            _CHECK_BODY
            and request.method in ("POST", "PUT", "PATCH", "DELETE")
        ):
            ct = (request.headers.get("content-type") or "").lower()
            if "application/json" in ct:
                body = await request.body()
                if len(body) > _MAX_BODY:
                    async def receive_large() -> dict[str, Any]:
                        return {"type": "http.request", "body": body, "more_body": False}

                    return await call_next(Request(request.scope, receive_large))
                if not body:
                    async def receive_empty() -> dict[str, Any]:
                        return {"type": "http.request", "body": b"", "more_body": False}

                    return await call_next(Request(request.scope, receive_empty))
                try:
                    data = json.loads(body)
                except json.JSONDecodeError:
                    async def receive_raw() -> dict[str, Any]:
                        return {"type": "http.request", "body": body, "more_body": False}

                    return await call_next(Request(request.scope, receive_raw))
                if isinstance(data, (dict, list)) and _scan_json_value(data):
                    return _bad_request()
                async def receive_ok() -> dict[str, Any]:
                    return {"type": "http.request", "body": body, "more_body": False}

                request = Request(request.scope, receive_ok)

        return await call_next(request)


def validate_sql_identifier(name: str, *, kind: str = "identifier") -> str:

    if not name or len(name) > 128:
        raise ValueError(f"Invalid {kind}")
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise ValueError(f"Invalid {kind}")
    return name
