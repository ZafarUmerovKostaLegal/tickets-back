
from __future__ import annotations

import time

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from infrastructure.auth_health import auth_service_is_healthy
from infrastructure.config import get_settings

router = APIRouter(tags=["ops"])

_START_MONO = time.monotonic()


@router.get("/live", summary="Liveness: процесс жив (без БД) — для оркестраторов")
def liveness():
    return {"status": "ok", "service": "gateway"}


@router.get("/ready", summary="Readiness: auth доступен (иначе 503)")
async def readiness():
    if not await auth_service_is_healthy():
        return JSONResponse(
            status_code=503,
            content={"ready": False, "detail": "auth_unavailable"},
        )
    return {"ready": True, "status": "healthy", "auth": "reachable"}


@router.get("/metrics", summary="Минимальные метрики (текст); для Prometheus положите exporter при необходимости")
def metrics():
    uptime = time.monotonic() - _START_MONO
    body = (
        "# HELP gateway_uptime_seconds Process uptime (monotonic)\n"
        "# TYPE gateway_uptime_seconds gauge\n"
        f'gateway_uptime_seconds{{service="gateway"}} {uptime:.3f}\n'
    )
    return Response(content=body, media_type="text/plain; charset=utf-8")
