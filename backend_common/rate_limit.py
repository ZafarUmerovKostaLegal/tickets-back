"""HTTP rate limiting middleware (gateway / auth).

Uses Redis when REDIS_URL is set; otherwise in-process counters (dev / single worker).
Not a substitute for edge WAF — defense-in-depth for API abuse and DoS amplification.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_log = logging.getLogger(__name__)

_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes")
_REDIS_URL = (os.getenv("REDIS_URL") or "").strip()


def _parse_limit(spec: str, default_count: int, default_window: int) -> tuple[int, int]:
    """Parse '120/minute' or '20/min' → (count, window_seconds)."""
    raw = (spec or "").strip().lower().replace(" ", "")
    if not raw:
        return default_count, default_window
    if "/" not in raw:
        try:
            return max(1, int(raw)), default_window
        except ValueError:
            return default_count, default_window
    count_s, unit = raw.split("/", 1)
    try:
        count = max(1, int(count_s))
    except ValueError:
        return default_count, default_window
    if unit in ("s", "sec", "second", "seconds"):
        return count, 1
    if unit in ("m", "min", "minute", "minutes"):
        return count, 60
    if unit in ("h", "hour", "hours"):
        return count, 3600
    return count, default_window


_DEFAULT_COUNT, _DEFAULT_WINDOW = _parse_limit(
    os.getenv("RATE_LIMIT_DEFAULT", "600/minute"), 600, 60
)
_AUTH_COUNT, _AUTH_WINDOW = _parse_limit(
    os.getenv("RATE_LIMIT_AUTH", "60/minute"), 60, 60
)
_REPORTS_COUNT, _REPORTS_WINDOW = _parse_limit(
    os.getenv("RATE_LIMIT_REPORTS", "300/minute"), 300, 60
)


def client_ip(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if forwarded:
        return forwarded
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def classify_path(path: str) -> tuple[str, int, int]:
    """Return (bucket_name, max_count, window_sec) for a request path."""
    p = (path or "").lower()
    auth_markers = (
        "/api/v1/auth/login",
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
        "/api/v1/auth/azure",
        "/auth/login",
        "/auth/token",
        "/auth/refresh",
        "/users/login",
    )
    if any(m in p for m in auth_markers):
        return "auth", _AUTH_COUNT, _AUTH_WINDOW
    if "/time-tracking/reports" in p or p.endswith("/reports") or "/reports/" in p:
        return "reports", _REPORTS_COUNT, _REPORTS_WINDOW
    return "default", _DEFAULT_COUNT, _DEFAULT_WINDOW


class _MemoryCounter:
    """Fixed-window counter shared in-process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, limit: int, window_sec: int) -> tuple[bool, int, int]:
        """Returns (allowed, remaining, retry_after_sec)."""
        now = time.monotonic()
        with self._lock:
            count, reset_at = self._data.get(key, (0, now + window_sec))
            if now >= reset_at:
                count, reset_at = 0, now + window_sec
            count += 1
            self._data[key] = (count, reset_at)
            # Opportunistic cleanup
            if len(self._data) > 10_000:
                expired = [k for k, (_, exp) in self._data.items() if now >= exp]
                for k in expired[:2000]:
                    self._data.pop(k, None)
            retry = max(1, int(reset_at - now + 0.999))
            if count > limit:
                return False, 0, retry
            return True, max(0, limit - count), retry


_MEMORY = _MemoryCounter()
_REDIS_CLIENT: Any = None
_REDIS_FAILED = False


def _redis_client() -> Any | None:
    global _REDIS_CLIENT, _REDIS_FAILED
    if _REDIS_FAILED or not _REDIS_URL:
        return None
    if _REDIS_CLIENT is not None:
        return _REDIS_CLIENT
    try:
        import redis

        _REDIS_CLIENT = redis.Redis.from_url(_REDIS_URL, decode_responses=True, socket_connect_timeout=0.5)
        _REDIS_CLIENT.ping()
        return _REDIS_CLIENT
    except Exception as e:
        _log.debug("rate_limit: redis unavailable (%s), using memory", e)
        _REDIS_FAILED = True
        return None


def hit_limit(key: str, limit: int, window_sec: int) -> tuple[bool, int, int]:
    client = _redis_client()
    if client is not None:
        try:
            pipe = client.pipeline()
            rkey = f"rl:{key}"
            pipe.incr(rkey)
            pipe.ttl(rkey)
            results = pipe.execute()
            count = int(results[0])
            ttl = int(results[1])
            if ttl < 0:
                client.expire(rkey, window_sec)
                ttl = window_sec
            if count > limit:
                return False, 0, max(1, ttl)
            return True, max(0, limit - count), max(1, ttl)
        except Exception as e:
            _log.debug("rate_limit redis error: %s", e)
    return _MEMORY.hit(key, limit, window_sec)


def reset_memory_for_tests() -> None:
    """Test helper — clear in-process counters."""
    with _MEMORY._lock:
        _MEMORY._data.clear()


def _too_many(retry_after: int) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests"},
        headers={"Retry-After": str(max(1, retry_after))},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limits; stricter buckets for auth and reports paths."""

    def __init__(
        self,
        app: Any,
        *,
        service_name: str = "api",
        skip_paths: frozenset[str] | None = None,
    ) -> None:
        super().__init__(app)
        self._service = service_name
        self._skip = skip_paths or frozenset({"/health", "/api/v1/health", "/ready", "/live"})

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        if not _ENABLED:
            return await call_next(request)
        if request.scope.get("type") != "http":
            return await call_next(request)
        if request.method == "OPTIONS":
            return await call_next(request)
        path = request.url.path or ""
        if path in self._skip or path.rstrip("/") in self._skip:
            return await call_next(request)

        bucket, limit, window = classify_path(path)
        ip = client_ip(request)
        key = f"{self._service}:{bucket}:{ip}"
        allowed, _remaining, retry_after = hit_limit(key, limit, window)
        if not allowed:
            return _too_many(retry_after)
        return await call_next(request)
