from datetime import datetime, timezone

from fastapi import APIRouter

from infrastructure.config import get_settings
from infrastructure.database import async_session_factory
from infrastructure.repositories import ExpenseRepository
from presentation.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", response_model=HealthResponse)
async def health():

    ok = False
    try:
        async with async_session_factory() as session:
            repo = ExpenseRepository(session)
            ok = await repo.health_check()
    except Exception:
        ok = False
    return HealthResponse(
        status="healthy" if ok else "degraded",
        service=get_settings().service_name,
        timestamp=datetime.now(timezone.utc),
    )
