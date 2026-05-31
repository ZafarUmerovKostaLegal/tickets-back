

from __future__ import annotations

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/smart-home", tags=["smart_home"])


def _base() -> str:
    return (get_settings().smart_home_service_url or "").rstrip("/")


def _strip_hop_and_cors(h: dict[str, str]) -> dict[str, str]:
    drop = {
        "content-encoding",
        "transfer-encoding",
        "connection",
        "content-length",
    }
    return {k: v for k, v in h.items() if k.lower() not in drop}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_smart_home(
    request: Request,
    path: str,
    authorization: str | None = Header(None, alias="Authorization"),
):
    if request.method.upper() != "OPTIONS":
        await verify_bearer_and_get_user(request, authorization)
    """Прокси к локальному Smart Home API (по умолчанию :8765).

    Путь после /api/v1/smart-home/ передаётся на upstream как есть:
      GET /api/v1/smart-home/scenes  →  GET {SMART_HOME_SERVICE_URL}/scenes
    """
    base = _base()
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "SMART_HOME_SERVICE_URL not configured",
                "hint": (
                    "Задайте SMART_HOME_SERVICE_URL, например "
                    "http://host.docker.internal:8765 или http://192.168.x.x:8765"
                ),
            },
        )
    url = f"{base}/{path.lstrip('/')}" if path else base
    if request.url.query:
        url = f"{url}?{request.url.query}"
    raw_headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length")
    }
    headers = merge_upstream_headers(raw_headers) or raw_headers
    try:
        body = await request.body()
    except Exception:
        body = b""
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            r = await client.request(
                method=request.method,
                url=url,
                headers=headers,
                content=body,
            )
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Smart Home service unreachable from gateway",
                "smart_home_service_url": base,
                "error": str(e),
                "hint": (
                    "Убедитесь, что API на :8765 запущен и доступен с хоста gateway "
                    "(firewall, bind 0.0.0.0, для Docker — host.docker.internal)."
                ),
            },
        )
    response_headers = _strip_hop_and_cors(dict(r.headers))
    return FastAPIResponse(
        content=r.content,
        status_code=r.status_code,
        headers=response_headers,
    )
