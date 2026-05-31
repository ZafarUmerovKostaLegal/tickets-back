import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from application.use_cases import GetHealthUseCase
from infrastructure.database import get_session
from infrastructure.repositories import HealthRepository
from infrastructure.config import get_settings
from presentation.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


async def get_health_use_case(session: AsyncSession = Depends(get_session)) -> GetHealthUseCase:
    return GetHealthUseCase(HealthRepository(session))


@router.get("", response_model=HealthResponse)
async def health(uc: GetHealthUseCase = Depends(get_health_use_case)):
    entity = await uc.execute(get_settings().service_name)
    return HealthResponse(
        status=entity.status,
        service=entity.service,
        timestamp=entity.timestamp,
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
    return JSONResponse(
        content={
            "status": "ok",
            "call_schedule": "reachable",
            "call_schedule_service_url": base,
            "api_prefix": "/api/v1/call-schedule",
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
