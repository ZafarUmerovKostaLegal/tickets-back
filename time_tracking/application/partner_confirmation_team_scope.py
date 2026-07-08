from __future__ import annotations

from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from application.report_builder import _base_entry_conditions
from infrastructure.models import TimeEntryModel
from infrastructure.repository_teams import TeamRepository


async def list_team_member_auth_user_ids_for_partner(
    session: AsyncSession,
    partner_auth_user_id: int,
) -> set[int]:
    repo = TeamRepository(session)
    ids = await repo.list_member_auth_user_ids_for_partner(partner_auth_user_id)
    return {int(x) for x in ids if int(x) > 0}


async def list_report_auth_user_ids_for_project_period(
    session: AsyncSession,
    *,
    project_id: str,
    date_from: date,
    date_to: date,
) -> set[int]:
    pid = (project_id or "").strip()
    if not pid or date_to < date_from:
        return set()
    cond = _base_entry_conditions(
        date_from,
        date_to,
        None,
        [pid],
        None,
        True,
    )
    q = select(TimeEntryModel.auth_user_id).where(and_(*cond)).distinct()
    rows = (await session.execute(q)).scalars().all()
    return {int(x) for x in rows if x is not None and int(x) > 0}


def partner_team_overlaps_report(
    *,
    team_member_ids: set[int],
    report_user_ids: set[int],
) -> bool:
    if not team_member_ids:
        return False
    if not report_user_ids:
        return False
    return bool(team_member_ids & report_user_ids)
