from __future__ import annotations

import sys

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/files", tags=["file_manager"])

_FILE_MANAGER_503_HINT = (
    "Gateway не достучался до микросервиса File Manager. Проверьте: "
    "1) контейнер file_manager запущен; "
    "2) FILE_MANAGER_SERVICE_URL=http://file_manager:1255; "
    "3) GET /health/file-manager"
)

_HOP_REQUEST_TO_UPSTREAM = frozenset(
    {
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
    }
)


def _file_manager_base() -> str:
    return (get_settings().file_manager_service_url or "").rstrip("/")


def _upstream_503(base: str, exc: httpx.RequestError | None = None) -> JSONResponse:
    payload: dict = {
        "detail": "File Manager service unavailable",
        "hint": _FILE_MANAGER_503_HINT,
        "file_manager_service_url": base,
    }
    if exc is not None:
        payload["upstream_error"] = type(exc).__name__
        payload["upstream_message"] = str(exc)[:500]
        print(f"[gateway] file_manager upstream RequestError: base={base!r} {exc!r}", file=sys.stderr, flush=True)
    return JSONResponse(status_code=503, content=payload)


def _request_headers_for_upstream(request: Request) -> dict[str, str]:
    return {k: v for k, v in request.headers.items() if k.lower() not in _HOP_REQUEST_TO_UPSTREAM}


def _strip_hop_and_cors(headers: dict) -> dict:
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
    return {k: v for k, v in headers.items() if k.lower() not in skip}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_file_manager(request: Request, path: str):
    base = _file_manager_base()
    if not base:
        return JSONResponse(status_code=503, content={"detail": "FILE_MANAGER_SERVICE_URL is not configured"})
    upstream_url = f"{base}/api/v1/files/{path}".rstrip("/") if path else f"{base}/api/v1/files"
    query = request.url.query
    if query:
        upstream_url = f"{upstream_url}?{query}"
    headers = merge_upstream_headers(_request_headers_for_upstream(request))
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.RequestError as exc:
        return _upstream_503(base, exc)
    out_headers = _strip_hop_and_cors(dict(upstream.headers))
    if upstream.status_code == 204:
        return FastAPIResponse(status_code=204, headers=out_headers)
    return FastAPIResponse(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )
