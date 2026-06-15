

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from application.access_control import ensure_time_entry_subject_allowed
from application.weekly_submission_service import (
    _submit_tz,
    submit_reporting_week_for_user,
)
from application.weekly_period import local_today
from infrastructure.database import get_session
from infrastructure.repositories import TimeTrackingUserRepository
from infrastructure.repository_weekly_submissions import WeeklySubmissionRepository
from infrastructure.report_cache import invalidate_all_reports
from presentation.deps import require_bearer_user

router = APIRouter(prefix="/users", tags=["weekly_submissions"])


class WeeklySubmissionSubmitBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    work_date: date | None = Field(
        None,
        alias="workDate",
        description="Любой день рабочей недели (сб–пт); по умолчанию — сегодня в WEEKLY_SUBMIT_TZ",
    )


class WeeklySubmissionOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    auth_user_id: int = Field(..., serialization_alias="authUserId")
    week_start: date = Field(..., serialization_alias="weekStart")
    week_end: date = Field(..., serialization_alias="weekEnd")
    status: str = "submitted"
    created: bool = Field(
        ...,
        description="True если сдача создана впервые; False если неделя уже была сдана",
    )


async def _ensure_user(session: AsyncSession, auth_user_id: int) -> None:
    ur = TimeTrackingUserRepository(session)
    if not await ur.get_by_auth_user_id(auth_user_id):
        raise HTTPException(status_code=404, detail="Пользователь не найден")


@router.get(
    "/{auth_user_id}/weekly-submissions",
    response_model=list[WeeklySubmissionOut],
    summary="Сданные рабочие недели (суббота–пятница) для блокировки правок на фронте",
)
async def list_weekly_submissions(
    auth_user_id: int,
    date_from: date | None = Query(None, alias="from"),
    date_to: date | None = Query(None, alias="to"),
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> list[WeeklySubmissionOut]:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=False)
    await _ensure_user(session, auth_user_id)
    if date_from is not None and date_to is not None and date_to < date_from:
        raise HTTPException(status_code=400, detail="Параметр to не может быть раньше from")
    repo = WeeklySubmissionRepository(session)
    rows = await repo.list_for_user_in_range(auth_user_id, date_from, date_to)
    return [
        WeeklySubmissionOut(
            auth_user_id=auth_user_id,
            week_start=r.week_start,
            week_end=r.week_end,
            status=r.status,
            created=False,
        )
        for r in rows
    ]


@router.post(
    "/{auth_user_id}/weekly-submissions",
    response_model=WeeklySubmissionOut,
    summary="Сдать рабочую неделю (суббота–пятница) на утверждение",
)
async def submit_weekly_time(
    auth_user_id: int,
    body: WeeklySubmissionSubmitBody | None = None,
    session: AsyncSession = Depends(get_session),
    viewer: dict = Depends(require_bearer_user),
) -> WeeklySubmissionOut:
    await ensure_time_entry_subject_allowed(session, viewer, auth_user_id, write=True)
    await _ensure_user(session, auth_user_id)

    anchor = (body.work_date if body is not None and body.work_date else None)
    if anchor is None:
        anchor = local_today(_submit_tz())

    w0, w1, created = await submit_reporting_week_for_user(
        session, auth_user_id, anchor_date=anchor
    )
    await session.commit()
    invalidate_all_reports()
    return WeeklySubmissionOut(
        auth_user_id=auth_user_id,
        week_start=w0,
        week_end=w1,
        created=created,
    )
