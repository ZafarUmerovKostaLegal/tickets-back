from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.repositories import HealthRepository

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


@router.get("", response_model=HealthResponse)
async def health():
    """Liveness — no DB. Gateway and Docker healthcheck use this."""
    return HealthResponse(
        status="ok",
        service=get_settings().service_name,
        timestamp=datetime.now(timezone.utc),
    )


@router.get("/ready", response_model=HealthResponse)
async def health_ready(session: AsyncSession = Depends(get_session)):
    """Readiness — requires a working database connection."""
    ok = await HealthRepository(session).check()
    return HealthResponse(
        status="ok" if ok else "degraded",
        service=get_settings().service_name,
        timestamp=datetime.now(timezone.utc),
    )
