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
