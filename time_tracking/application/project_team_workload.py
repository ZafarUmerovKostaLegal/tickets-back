

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from application.team_workload_builder import build_team_workload_members_and_summary
from application.team_workload_math import period_days_inclusive
from infrastructure.repositories import (
    ClientProjectRepository,
    TimeEntryRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)
from presentation.schemas import TeamWorkloadOut


async def compute_project_team_workload(
    session: AsyncSession,
    *,
    client_id: str,
    project_id: str,
    date_from: date,
    date_to: date,
    include_archived: bool,
) -> TeamWorkloadOut | None:

    proj_repo = ClientProjectRepository(session)
    row = await proj_repo.get_by_id(client_id, project_id)
    if not row:
        return None

    pdays = period_days_inclusive(date_from, date_to)
    user_repo = TimeTrackingUserRepository(session)
    entry_repo = TimeEntryRepository(session)
    access_repo = UserProjectAccessRepository(session)

    from_access = await access_repo.list_auth_user_ids_for_project(project_id)
    from_entries = await entry_repo.list_auth_users_with_entries_on_project(
        date_from, date_to, project_id
    )
    member_ids = sorted(set(from_access) | set(from_entries))

    users = await user_repo.list_users()
    by_id = {u.auth_user_id: u for u in users}

    sums = await entry_repo.aggregate_by_user_for_project(date_from, date_to, project_id)

    member_rows = []
    for uid in member_ids:
        u = by_id.get(uid)
        if u is None:
            continue
        if u.is_blocked:
            continue
        if not include_archived and u.is_archived:
            continue
        member_rows.append(u)

    members, summary = build_team_workload_members_and_summary(
        member_rows,
        sums,
        date_from=date_from,
        date_to=date_to,
    )

    return TeamWorkloadOut(
        date_from=date_from,
        date_to=date_to,
        period_days=pdays,
        summary=summary,
        members=members,
        project_id=project_id,
        client_id=client_id,
        project_name=row.name,
    )
