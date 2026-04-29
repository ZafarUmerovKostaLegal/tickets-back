from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from application.use_cases import GetHealthUseCase
from infrastructure.config import get_settings
from infrastructure.database import get_session
from infrastructure.repositories import HealthRepository
from presentation.schemas import HealthResponse

router = APIRouter(prefix="/health", tags=["health"])


async def get_health_use_case(
    session: AsyncSession = Depends(get_session),
) -> GetHealthUseCase:
    return GetHealthUseCase(HealthRepository(session))


@router.get("", response_model=HealthResponse)
async def health(uc: GetHealthUseCase = Depends(get_health_use_case)):
    entity = await uc.execute(get_settings().service_name)
    return HealthResponse(
        status=entity.status,
        service=entity.service,
        timestamp=entity.timestamp,
    )
