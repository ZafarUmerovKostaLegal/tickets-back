from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from infrastructure.database import get_session
from infrastructure.config import get_settings
from presentation.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)):
    try:
        await session.execute(text("SELECT 1"))
        status = "healthy"
    except Exception:
        status = "degraded"
    return HealthResponse(
        status=status,
        service=get_settings().service_name,
        timestamp=datetime.utcnow(),
    )
