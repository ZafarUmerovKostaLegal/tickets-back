from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from infrastructure.config import get_settings
from infrastructure.database import is_db_disabled, is_db_ready

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime
    database: str = "unknown"


@router.get("", response_model=HealthResponse)
async def health():
    """Liveness — never touches Postgres. Used by Docker and gateway."""
    if is_db_disabled():
        db = "disabled"
    elif is_db_ready():
        db = "ready"
    else:
        db = "pending"
    return HealthResponse(
        status="ok",
        service=get_settings().service_name,
        timestamp=datetime.now(timezone.utc),
        database=db,
    )


@router.get("/ready", response_model=HealthResponse)
async def health_ready():
    """Readiness — reports whether DB init finished (no query if disabled)."""
    if is_db_disabled():
        return HealthResponse(
            status="degraded",
            service=get_settings().service_name,
            timestamp=datetime.now(timezone.utc),
            database="disabled",
        )
    if not is_db_ready():
        return HealthResponse(
            status="degraded",
            service=get_settings().service_name,
            timestamp=datetime.now(timezone.utc),
            database="pending",
        )
    return HealthResponse(
        status="ok",
        service=get_settings().service_name,
        timestamp=datetime.now(timezone.utc),
        database="ready",
    )
