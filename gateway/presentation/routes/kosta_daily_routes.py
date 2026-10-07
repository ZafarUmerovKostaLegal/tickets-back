from __future__ import annotations

import sys

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/kosta-daily", tags=["kosta-daily"])

_KOSTA_DAILY_503_HINT = (
    "Gateway не достучался до микросервиса kosta_daily. Проверьте: "
    "1) контейнер kosta_daily запущен; "
    "2) KOSTA_DAILY_SERVICE_URL=http://kosta_daily:1251; "
    "3) GET /health/kosta-daily"
)


def _kosta_daily_base() -> str:
    return (get_settings().kosta_daily_service_url or "").rstrip("/")


def _kosta_daily_upstream_503(base: str, exc: httpx.RequestError | None = None) -> JSONResponse:
    payload: dict = {
        "detail": "Kosta Daily service unavailable",
        "hint": _KOSTA_DAILY_503_HINT,
        "kosta_daily_service_url": base,
    }
    if exc is not None:
        payload["upstream_error"] = type(exc).__name__
        payload["upstream_message"] = str(exc)[:500]
        print(
            f"[gateway] kosta_daily upstream RequestError: base={base!r} {exc!r}",
            file=sys.stderr,
            flush=True,
        )
    return JSONResponse(status_code=503, content=payload)


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


def _request_headers_for_upstream(request: Request) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _HOP_REQUEST_TO_UPSTREAM:
            continue
        out[key] = value
    return out


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
@router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_kosta_daily(request: Request, path: str = ""):
    base = _kosta_daily_base()
    if not base:
        return JSONResponse(
            status_code=503,
            content={"detail": "KOSTA_DAILY_SERVICE_URL is not configured"},
        )
    upstream_url = f"{base}/api/v1/kosta-daily/{path}".rstrip("/")
    if not path:
        upstream_url = f"{base}/api/v1/kosta-daily"
    query = request.url.query
    if query:
        upstream_url = f"{upstream_url}?{query}"
    headers = merge_upstream_headers(_request_headers_for_upstream(request))
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                upstream_url,
                headers=headers,
                content=body if body else None,
            )
    except httpx.RequestError as exc:
        return _kosta_daily_upstream_503(base, exc)
    out_headers = _strip_hop_and_cors(dict(upstream.headers))
    if upstream.status_code == 204:
        return FastAPIResponse(status_code=204, headers=out_headers)
    return FastAPIResponse(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=out_headers,
        media_type=upstream.headers.get("content-type"),
    )
