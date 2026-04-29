from datetime import datetime
from fastapi import APIRouter, Depends
from infrastructure.config import get_settings
from presentation.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health():

    settings = get_settings()
    return HealthResponse(
        status="healthy",
        service=settings.service_name,
        timestamp=datetime.utcnow(),
    )
