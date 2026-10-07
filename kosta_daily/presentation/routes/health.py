from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import BaseModel

from infrastructure.config import get_settings

router = APIRouter(prefix="/health", tags=["health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    timestamp: datetime


@router.get("", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service=get_settings().service_name,
        timestamp=datetime.now(timezone.utc),
    )
