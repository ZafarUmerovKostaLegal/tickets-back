"""Read-only integrity audits — never delete or mutate rows."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from infrastructure.models import TimeEntryModel, TimeManagerClientProjectModel, TimeTrackingUserModel


async def audit_tt_integrity(session: AsyncSession) -> dict[str, Any]:
    """Count potential integrity issues without changing data."""

    users_total = int(
        (await session.execute(select(func.count()).select_from(TimeTrackingUserModel))).scalar_one()
        or 0
    )
    users_archived = int(
        (
            await session.execute(
                select(func.count()).select_from(TimeTrackingUserModel).where(
                    TimeTrackingUserModel.is_archived.is_(True)
                )
            )
        ).scalar_one()
        or 0
    )

    # Entries whose auth_user_id is missing from time_tracking_users (logical orphan).
    orphan_entries_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_entries e
            WHERE NOT EXISTS (
                SELECT 1 FROM time_tracking_users u
                WHERE u.auth_user_id = e.auth_user_id
            )
            """
        )
    )
    orphan_entry_users = int(orphan_entries_r.scalar_one() or 0)

    orphan_project_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_entries e
            WHERE e.project_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM time_tracking_client_projects p
                WHERE p.id = e.project_id
              )
            """
        )
    )
    orphan_entry_projects = int(orphan_project_r.scalar_one() or 0)

    projects_total = int(
        (
            await session.execute(select(func.count()).select_from(TimeManagerClientProjectModel))
        ).scalar_one()
        or 0
    )
    entries_total = int(
        (await session.execute(select(func.count()).select_from(TimeEntryModel))).scalar_one() or 0
    )

    return {
        "readOnly": True,
        "mutatesData": False,
        "usersTotal": users_total,
        "usersArchived": users_archived,
        "projectsTotal": projects_total,
        "entriesTotal": entries_total,
        "orphanEntryUserRefs": orphan_entry_users,
        "orphanEntryProjectRefs": orphan_entry_projects,
        "note": (
            "Counts only. Orphans are not deleted automatically — "
            "fix them manually after backup if needed."
        ),
    }
