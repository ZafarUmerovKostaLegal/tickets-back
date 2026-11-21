"""Reject request paths that could escape the route they were authorised for.

Gateway proxies paste path parameters into upstream URLs, and httpx resolves dot segments, so
``/api/v1/vacations/leave-requests/%2E%2E/schedule/employees/5`` would pass a self-service check
and land on ``/schedule/employees/5`` upstream. Legitimate clients never send dot segments, so
any such path is refused before routing, for HTTP and WebSocket alike.
"""

from __future__ import annotations

import json
import re
from urllib.parse import unquote

# Endpoints that only other services may call (they talk to the upstream directly, not via gateway).
_INTERNAL_ONLY_PREFIXES: tuple[str, ...] = (
    "/api/v1/expenses/report-data",
)

_MULTI_SLASH = re.compile(r"/{2,}")


def _decoded_variants(path: str) -> list[str]:
    """The path as routed plus up to two further percent-decoding passes (double encoding)."""
    out = [path]
    current = path
    for _ in range(2):
        nxt = unquote(current)
        if nxt == current:
            break
        out.append(nxt)
        current = nxt
    return out


def path_is_suspicious(path: str) -> bool:
    for variant in _decoded_variants(path or ""):
        if "\x00" in variant:
            return True
        for segment in variant.replace("\\", "/").split("/"):
            if segment.strip() in {".", ".."}:
                return True
    return False


def path_is_internal_only(path: str) -> bool:
    for variant in _decoded_variants(path or ""):
        norm = _MULTI_SLASH.sub("/", variant.replace("\\", "/")).lower()
        norm = norm.split("?", 1)[0].split("#", 1)[0]
        for prefix in _INTERNAL_ONLY_PREFIXES:
            if norm == prefix or norm.startswith(prefix + "/") or norm.startswith(prefix + ";"):
                return True
    return False


class PathGuardMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        raw = scope.get("raw_path")
        raw_path = raw.decode("latin-1") if isinstance(raw, (bytes, bytearray)) else ""
        if path_is_suspicious(path) or path_is_suspicious(raw_path):
            await _reject(scope, receive, send, 400, "Invalid path")
            return
        if path_is_internal_only(path) or path_is_internal_only(raw_path):
            await _reject(scope, receive, send, 404, "Not Found")
            return
        await self.app(scope, receive, send)


async def _reject(scope, receive, send, status: int, detail: str) -> None:
    if scope.get("type") == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
