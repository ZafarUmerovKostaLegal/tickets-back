from __future__ import annotations

import sys
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/file-guard", tags=["file-guard"])

_HINT = (
    "Gateway не достучался до микросервиса file_guard. Проверьте контейнер file_guard "
    "и FILE_GUARD_SERVICE_URL=http://file_guard:1253"
)

_HOP = frozenset({
    "host",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "proxy-connection",
    "proxy-authenticate",
    "proxy-authorization",
    "upgrade",
    "content-length",
})


def _base() -> str:
    return (get_settings().file_guard_service_url or "").rstrip("/")


def safe_upstream_path(path: str) -> str | None:
    raw = unquote((path or "").replace("\\", "/")).strip()
    if not raw or raw.startswith("/") or "\x00" in raw or "?" in raw or "#" in raw:
        return None
    parts = raw.split("/")
    if any(part in {"", ".", ".."} or ":" in part or part.startswith("@") for part in parts):
        return None
    return "/".join(parts)


def _unavailable(base: str, exc: httpx.RequestError | None = None) -> JSONResponse:
    payload: dict = {"detail": "File guard service unavailable", "hint": _HINT}
    if exc is not None:
        print(f"[gateway] file_guard upstream RequestError: base={base!r} {exc!r}", file=sys.stderr, flush=True)
    return JSONResponse(status_code=503, content=payload)


def _headers(request: Request) -> dict[str, str]:
    raw = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP
    }
    return merge_upstream_headers(raw) or raw


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_file_guard(request: Request, path: str):
    base = _base()
    if not base:
        return _unavailable("")
    clean_path = safe_upstream_path(path)
    if clean_path is None:
        return JSONResponse(status_code=400, content={"detail": "Некорректный путь"})
    url = f"{base}/api/v1/file-guard/{clean_path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    timeout = 120.0 if path.startswith("agent/") else 30.0
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                url,
                headers=_headers(request),
                content=await request.body(),
            )
    except httpx.RequestError as exc:
        return _unavailable(base, exc)
    skip = {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "content-encoding",
        "access-control-allow-origin",
        "access-control-allow-credentials",
        "access-control-allow-methods",
        "access-control-allow-headers",
    }
    out_headers = {key: value for key, value in upstream.headers.items() if key.lower() not in skip}
    if upstream.status_code == 204:
        return FastAPIResponse(status_code=204, headers=out_headers)
    return FastAPIResponse(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )
