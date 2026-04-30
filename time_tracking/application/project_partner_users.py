
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.auth_user_directory import fetch_auth_user_partner_hints_by_id
from application.project_partner_requirement import user_satisfies_partner_rule
from infrastructure.models import TimeTrackingUserModel
from infrastructure.repository_access import UserProjectAccessRepository


async def list_partner_auth_user_ids_for_project(
    session: AsyncSession,
    access_repo: UserProjectAccessRepository,
    project_id: str,
    *,
    authorization: str | None = None,
) -> list[int]:

    pid = (project_id or "").strip()
    if not pid:
        return []
    uids = await access_repo.list_auth_user_ids_for_project(pid)
    if not uids:
        return []
    auth_hints: dict[int, dict[str, str | None]] = {}
    if (authorization or "").strip():
        auth_hints = await fetch_auth_user_partner_hints_by_id(authorization or "")
    r = await session.execute(
        select(TimeTrackingUserModel.auth_user_id, TimeTrackingUserModel.position).where(
            TimeTrackingUserModel.auth_user_id.in_(uids)
        )
    )
    by_uid: dict[int, str | None] = {int(a): b for a, b in r.all()}
    partners: list[int] = []
    for uid in uids:
        uid_i = int(uid)
        hint = auth_hints.get(uid_i) or {}
        if user_satisfies_partner_rule(
            by_uid.get(uid_i),
            hint.get("position"),
            hint.get("role"),
        ):
            partners.append(uid_i)
    return sorted(set(partners))
