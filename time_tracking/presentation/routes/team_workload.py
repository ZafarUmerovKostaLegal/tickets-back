

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from application.report_builder import load_week_submitted_user_dates, _load_initials_map
from application.team_workload_builder import build_team_workload_members_and_summary
from application.team_workload_math import period_days_inclusive
from infrastructure.database import get_session
from infrastructure.repositories import TimeEntryRepository, TimeTrackingUserRepository
from presentation.schemas import (
    TeamWorkloadOut,
)

router = APIRouter(prefix="/team-workload", tags=["team_workload"])


@router.get("", response_model=TeamWorkloadOut)
async def get_team_workload(
    date_from: date = Query(..., alias="from"),
    date_to: date = Query(..., alias="to"),
    include_archived: bool = Query(False, alias="includeArchived"),
    session: AsyncSession = Depends(get_session),
) -> TeamWorkloadOut:
    if date_to < date_from:
        raise HTTPException(status_code=400, detail="Параметр to не может быть раньше from")
    try:
        pdays = period_days_inclusive(date_from, date_to)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    user_repo = TimeTrackingUserRepository(session)
    entry_repo = TimeEntryRepository(session)

    users = await user_repo.list_users()
    rows = [u for u in users if (include_archived or not u.is_archived) and not u.is_blocked]

    sums = await entry_repo.aggregate_by_user(date_from, date_to)
    entry_counts = await entry_repo.count_active_by_user(date_from, date_to)
    uids = {u.auth_user_id for u in rows}
    submitted_dates = await load_week_submitted_user_dates(session, uids, date_from, date_to)
    initials_map = await _load_initials_map(session)

    members, summary = build_team_workload_members_and_summary(
        rows,
        sums,
        date_from=date_from,
        date_to=date_to,
        entry_counts=entry_counts,
        submitted_user_dates=submitted_dates,
        initials_map=initials_map,
    )

    return TeamWorkloadOut(
        date_from=date_from,
        date_to=date_to,
        period_days=pdays,
        summary=summary,
        members=members,
    )
