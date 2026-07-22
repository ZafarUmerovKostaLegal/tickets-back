import httpx
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from infrastructure.auth_health import auth_service_is_healthy
from infrastructure.config import get_settings
from presentation.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health():
    healthy = await auth_service_is_healthy()
    return HealthResponse(
        status="healthy" if healthy else "degraded",
        service=get_settings().service_name,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/chat", summary="Проверка доступности микросервиса chat с gateway")
async def health_chat():
    base = (get_settings().chat_service_url or "").rstrip("/")
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "CHAT_SERVICE_URL not configured",
                "hint": "Задайте CHAT_SERVICE_URL, например http://chat:1246",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.get(f"{base}/health")
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Chat unreachable from gateway",
                "chat_service_url": base,
                "upstream_error": type(e).__name__,
                "upstream_message": str(e)[:500],
            },
        )
    if r.status_code != 200:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Chat /health not OK",
                "chat_service_url": base,
                "upstream_status": r.status_code,
            },
        )
    return JSONResponse(
        content={"status": "ok", "chat": "reachable", "chat_service_url": base}
    )


@router.get("/contacts", summary="Проверка доступности микросервиса contacts с gateway")
async def health_contacts():
    base = (get_settings().contacts_service_url or "").rstrip("/")
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "CONTACTS_SERVICE_URL not configured",
                "hint": "Задайте CONTACTS_SERVICE_URL, например http://contacts:1248",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.get(f"{base}/health")
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Contacts unreachable from gateway",
                "contacts_service_url": base,
                "upstream_error": type(e).__name__,
                "upstream_message": str(e)[:500],
            },
        )
    if r.status_code != 200:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Contacts /health not OK",
                "contacts_service_url": base,
                "upstream_status": r.status_code,
            },
        )
    return JSONResponse(
        content={"status": "ok", "contacts": "reachable", "contacts_service_url": base}
    )


@router.get("/correspondence", summary="Проверка доступности микросервиса correspondence с gateway")
async def health_correspondence():
    base = (get_settings().correspondence_service_url or "").rstrip("/")
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "CORRESPONDENCE_SERVICE_URL not configured",
                "hint": "Задайте CORRESPONDENCE_SERVICE_URL, например http://correspondence:1249",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.get(f"{base}/health")
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Correspondence unreachable from gateway",
                "correspondence_service_url": base,
                "upstream_error": type(e).__name__,
                "upstream_message": str(e)[:500],
            },
        )
    if r.status_code != 200:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Correspondence /health not OK",
                "correspondence_service_url": base,
                "upstream_status": r.status_code,
            },
        )
    return JSONResponse(
        content={"status": "ok", "correspondence": "reachable", "correspondence_service_url": base}
    )


@router.get("/time-tracking", summary="Проверка доступности time_tracking с gateway")
async def health_time_tracking():
    base = (get_settings().time_tracking_service_url or "").rstrip("/")
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "TIME_TRACKING_SERVICE_URL not configured",
                "hint": "Задайте TIME_TRACKING_SERVICE_URL, например http://time_tracking:1241",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.get(f"{base}/health")
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Time tracking unreachable from gateway",
                "time_tracking_service_url": base,
                "upstream_error": type(e).__name__,
                "upstream_message": str(e)[:500],
                "hint": (
                    "Проверьте: docker compose ps time_tracking; "
                    "docker compose logs time_tracking --tail 80"
                ),
            },
        )
    if r.status_code != 200:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Time tracking /health not OK",
                "time_tracking_service_url": base,
                "upstream_status": r.status_code,
                "upstream_body": r.text[:500],
            },
        )
    return JSONResponse(
        content={
            "status": "ok",
            "time_tracking": "reachable",
            "time_tracking_service_url": base,
        }
    )


@router.get("/todos", summary="Проверка доступности микросервиса todos с gateway")
async def health_todos():

    base = (get_settings().todos_service_url or "").rstrip("/")
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "TODOS_SERVICE_URL not configured",
                "hint": "Задайте TODOS_SERVICE_URL, например http://todos:1240",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.get(f"{base}/health")
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Todos unreachable from gateway",
                "todos_service_url": base,
                "upstream_error": type(e).__name__,
                "upstream_message": str(e)[:500],
                "hint": (
                    "Внутри контейнера gateway адрес localhost — это не todos. "
                    "Используйте имя сервиса Docker: TODOS_SERVICE_URL=http://todos:1240"
                ),
            },
        )
    if r.status_code != 200:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Todos /health not OK",
                "todos_service_url": base,
                "upstream_status": r.status_code,
            },
        )
    return JSONResponse(
        content={
            "status": "ok",
            "todos": "reachable",
            "todos_service_url": base,
        }
    )


@router.get("/call-schedule", summary="Проверка call_schedule с gateway")
async def health_call_schedule():
    base = (get_settings().call_schedule_service_url or "").rstrip("/")
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "CALL_SCHEDULE_SERVICE_URL not configured",
                "hint": "Задайте CALL_SCHEDULE_SERVICE_URL, например http://call_schedule:1245",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.get(f"{base}/health")
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Call schedule unreachable from gateway",
                "call_schedule_service_url": base,
                "upstream_error": type(e).__name__,
                "upstream_message": str(e)[:500],
            },
        )
    if r.status_code != 200:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Call schedule /health not OK",
                "call_schedule_service_url": base,
                "upstream_status": r.status_code,
            },
        )
    body: dict = {}
    try:
        raw = r.json()
        if isinstance(raw, dict):
            body = raw
    except Exception:
        body = {}
    graph_ok = body.get("graph_configured")
    mailbox_ok = body.get("mailbox_configured")
    if graph_ok is False or mailbox_ok is False:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Call schedule reachable but Graph/mailbox not configured",
                "call_schedule_service_url": base,
                "graph_configured": graph_ok,
                "mailbox_configured": mailbox_ok,
                "hint": (
                    "Задайте AZURE_CLIENT_ID/SECRET/TENANT_ID или MICROSOFT_* "
                    "для сервиса call_schedule и CALL_SCHEDULE_MAILBOX"
                ),
            },
        )
    return JSONResponse(
        content={
            "status": "ok",
            "call_schedule": "reachable",
            "call_schedule_service_url": base,
            "api_prefix": "/api/v1/call-schedule",
            "graph_configured": graph_ok,
            "mailbox_configured": mailbox_ok,
        }
    )


@router.get("/smart-home", summary="Проверка Smart Home API с gateway")
async def health_smart_home():
    base = (get_settings().smart_home_service_url or "").rstrip("/")
    if not base:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "SMART_HOME_SERVICE_URL not configured",
                "hint": "Задайте SMART_HOME_SERVICE_URL, например http://host.docker.internal:8765",
            },
        )
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            r = await client.get(base)
    except httpx.RequestError as e:
        return JSONResponse(
            status_code=503,
            content={
                "detail": "Smart Home unreachable from gateway",
                "smart_home_service_url": base,
                "upstream_error": type(e).__name__,
                "upstream_message": str(e)[:500],
            },
        )
    return JSONResponse(
        content={
            "status": "ok" if r.status_code < 500 else "degraded",
            "smart_home": "reachable",
            "smart_home_service_url": base,
            "upstream_status": r.status_code,
            "api_prefix": "/api/v1/smart-home",
        }
    )
