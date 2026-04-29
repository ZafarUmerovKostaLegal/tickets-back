from datetime import time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from application.use_cases import GetWorkdaySettingsUseCase, UpdateWorkdaySettingsUseCase
from application.ports import WorkdaySettingsRepositoryPort
from infrastructure.database import get_session
from infrastructure.repositories import WorkdaySettingsRepository
from presentation.schemas import WorkdaySettingsResponse, WorkdaySettingsUpdateRequest

router = APIRouter(prefix="/settings", tags=["settings"])


def get_settings_repo(session: AsyncSession = Depends(get_session)) -> WorkdaySettingsRepositoryPort:
    return WorkdaySettingsRepository(session)


@router.get("/workday", response_model=WorkdaySettingsResponse)
async def get_workday_settings(
    repo: WorkdaySettingsRepositoryPort = Depends(get_settings_repo),
):
    uc = GetWorkdaySettingsUseCase(repo)
    settings = await uc.execute()
    return WorkdaySettingsResponse(
        workday_start=settings.workday_start,
        workday_end=settings.workday_end,
        late_threshold_minutes=settings.late_threshold_minutes,
        daily_hours_norm=settings.daily_hours_norm,
    )


@router.patch("/workday", response_model=WorkdaySettingsResponse)
async def update_workday_settings(
    body: WorkdaySettingsUpdateRequest,
    repo: WorkdaySettingsRepositoryPort = Depends(get_settings_repo),
    session: AsyncSession = Depends(get_session),
):
    if body.workday_start and body.workday_end and body.workday_start >= body.workday_end:
        raise HTTPException(status_code=400, detail="Начало рабочего дня должно быть раньше конца.")
    if body.late_threshold_minutes is not None and body.late_threshold_minutes < 0:
        raise HTTPException(status_code=400, detail="Предел опоздания не может быть отрицательным.")
    if body.daily_hours_norm is not None and body.daily_hours_norm <= 0:
        raise HTTPException(status_code=400, detail="Норма часов в день должна быть больше нуля.")

    uc = UpdateWorkdaySettingsUseCase(repo)
    settings = await uc.execute(
        workday_start=body.workday_start,
        workday_end=body.workday_end,
        late_threshold_minutes=body.late_threshold_minutes,
        daily_hours_norm=body.daily_hours_norm,
    )
    await session.commit()
    return WorkdaySettingsResponse(
        workday_start=settings.workday_start,
        workday_end=settings.workday_end,
        late_threshold_minutes=settings.late_threshold_minutes,
        daily_hours_norm=settings.daily_hours_norm,
    )

