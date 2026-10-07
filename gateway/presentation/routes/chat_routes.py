from __future__ import annotations

import sys

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/chat", tags=["chat"])

_CHAT_503_HINT = (
    "Gateway не достучался до микросервиса chat. Проверьте: "
    "1) контейнер chat запущен; "
    "2) CHAT_SERVICE_URL=http://chat:1246; "
    "3) GET /health/chat"
)


def _chat_base() -> str:
    return (get_settings().chat_service_url or "").rstrip("/")


def _chat_upstream_503(base: str, exc: httpx.RequestError | None = None) -> JSONResponse:
    payload: dict = {
        "detail": "Chat service unavailable",
        "hint": _CHAT_503_HINT,
        "chat_service_url": base,
    }
    if exc is not None:
        payload["upstream_error"] = type(exc).__name__
        payload["upstream_message"] = str(exc)[:500]
        print(f"[gateway] chat upstream RequestError: base={base!r} {exc!r}", file=sys.stderr, flush=True)
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


def _request_headers_for_chat_upstream(request: Request) -> dict[str, str]:
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
async def proxy_chat(request: Request, path: str):
    base = _chat_base()
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "CHAT_SERVICE_URL not configured",
                "hint": "Задайте CHAT_SERVICE_URL для gateway, например http://chat:1246",
            },
        )
    url = f"{base}/api/v1/chat/{path}" if path else f"{base}/api/v1/chat"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    raw_headers = _request_headers_for_chat_upstream(request)
    headers = merge_upstream_headers(raw_headers) or raw_headers
    try:
        body = await request.body()
    except Exception:
        body = b""
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            r = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
            )
    except httpx.RequestError as e:
        return _chat_upstream_503(base, e)
    except Exception:
        return JSONResponse(status_code=502, content={"detail": "Bad gateway"})
    response_headers = _strip_hop_and_cors(dict(r.headers))
    return FastAPIResponse(
        content=r.content,
        status_code=r.status_code,
        headers=response_headers,
    )
