

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from application.use_cases import GetHealthUseCase
from infrastructure.database import get_session
from infrastructure.repositories import HealthRepository

router = APIRouter(tags=["ops"])

_START_MONO = time.monotonic()


async def get_health_use_case(session: AsyncSession = Depends(get_session)) -> GetHealthUseCase:
    return GetHealthUseCase(HealthRepository(session))


@router.get("/live", summary="Liveness: процесс жив (без БД) — для оркестраторов")
def liveness():
    return {"status": "ok", "service": "gateway"}


@router.get("/ready", summary="Readiness: БД gateway доступна (иначе 503)")
async def readiness(uc: GetHealthUseCase = Depends(get_health_use_case)):
    ent = await uc.execute("gateway")
    if ent.status != "healthy":
        return JSONResponse(
            status_code=503,
            content={"ready": False, "detail": "database_unavailable", "status": ent.status},
        )
    return {"ready": True, "status": ent.status}


@router.get("/metrics", summary="Минимальные метрики (текст); для Prometheus положите exporter при необходимости")
def metrics():
    uptime = time.monotonic() - _START_MONO
    body = (
        "# HELP gateway_uptime_seconds Process uptime (monotonic)\n"
        "# TYPE gateway_uptime_seconds gauge\n"
        f'gateway_uptime_seconds{{service="gateway"}} {uptime:.3f}\n'
    )
    return Response(content=body, media_type="text/plain; charset=utf-8")
