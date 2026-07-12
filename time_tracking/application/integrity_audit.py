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

    orphan_active_project_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_entries e
            WHERE e.project_id IS NOT NULL
              AND e.voided_at IS NULL
              AND NOT EXISTS (
                SELECT 1 FROM time_tracking_client_projects p
                WHERE p.id = e.project_id
              )
            """
        )
    )
    orphan_active_entry_projects = int(orphan_active_project_r.scalar_one() or 0)

    orphan_voided_project_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_entries e
            WHERE e.project_id IS NOT NULL
              AND e.voided_at IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM time_tracking_client_projects p
                WHERE p.id = e.project_id
              )
            """
        )
    )
    orphan_voided_entry_projects = int(orphan_voided_project_r.scalar_one() or 0)

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

    orphan_archive_project_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_entry_archives a
            WHERE a.project_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM time_tracking_client_projects p
                WHERE p.id = a.project_id
              )
            """
        )
    )
    orphan_archive_projects = int(orphan_archive_project_r.scalar_one() or 0)

    dangling_reports_to_r = await session.execute(
        text(
            """
            SELECT COUNT(*) FROM time_tracking_users u
            WHERE u.reports_to_auth_user_id IS NOT NULL
              AND NOT EXISTS (
                SELECT 1 FROM time_tracking_users m
                WHERE m.auth_user_id = u.reports_to_auth_user_id
              )
            """
        )
    )
    dangling_reports_to = int(dangling_reports_to_r.scalar_one() or 0)

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
        "orphanActiveEntryProjectRefs": orphan_active_entry_projects,
        "orphanVoidedEntryProjectRefs": orphan_voided_entry_projects,
        "orphanArchiveProjectRefs": orphan_archive_projects,
        "danglingReportsToUserRefs": dangling_reports_to,
        "note": (
            "Counts only. Orphans are not deleted automatically — "
            "fix them manually after backup if needed. "
            "Hard FK fk_tt_entries_project_id (ON DELETE RESTRICT) is applied by "
            "schema patch time_entries_project_id_fk when orphanEntryProjectRefs is 0."
        ),
    }
