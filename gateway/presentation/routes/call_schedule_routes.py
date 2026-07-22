

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, Response as FastAPIResponse

from infrastructure.auth_upstream import verify_bearer_and_get_user
from infrastructure.config import get_settings
from infrastructure.upstream_auth_context import merge_upstream_headers

router = APIRouter(prefix="/api/v1/call-schedule", tags=["call_schedule"])


def _base() -> str:
    return (get_settings().call_schedule_service_url or "").rstrip("/")


def _strip_hop_and_cors(h: dict[str, str]) -> dict[str, str]:
    drop = {
        "content-encoding",
        "transfer-encoding",
        "connection",
        "content-length",
    }
    return {k: v for k, v in h.items() if k.lower() not in drop}


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_call_schedule(
    request: Request,
    path: str,
    authorization: str | None = Header(None, alias="Authorization"),
):
    if request.method.upper() != "OPTIONS":
        await verify_bearer_and_get_user(request, authorization)
    base = _base()
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "CALL_SCHEDULE_SERVICE_URL not configured",
                "hint": "Задайте CALL_SCHEDULE_SERVICE_URL, например http://call_schedule:1245",
            },
        )
    url = f"{base}/api/v1/call-schedule/{path}" if path else f"{base}/api/v1/call-schedule"
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
        err = str(e)
        hint = (
            "Контейнер call_schedule не запущен или не в сети gateway. "
            "На сервере: docker compose ps call_schedule && docker compose up -d --build call_schedule"
        )
        if "name resolution" in err.lower() or "Errno -3" in err or "Name or service not known" in err:
            hint = (
                "DNS не резолвит имя call_schedule — сервиса нет в текущем Docker Compose / Portainer stack. "
                "Добавьте и запустите сервис call_schedule из docker-compose.yml "
                "(порт 1245), затем перезапустите gateway при необходимости."
            )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Call schedule service unreachable",
                "call_schedule_service_url": base,
                "error": err,
                "hint": hint,
            },
        )
    response_headers = _strip_hop_and_cors(dict(r.headers))
    return FastAPIResponse(
        content=r.content,
        status_code=r.status_code,
        headers=response_headers,
    )
