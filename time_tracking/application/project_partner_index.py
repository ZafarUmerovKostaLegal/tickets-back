from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.auth_user_directory import fetch_auth_user_partner_hints_by_id
from application.project_partner_requirement import user_satisfies_partner_rule
from infrastructure.models import TimeTrackingUserModel
from infrastructure.repository_access import UserProjectAccessRepository


async def build_project_partner_participant_index(
    session: AsyncSession,
    project_ids: list[str],
    *,
    authorization: str | None = None,
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """Партнёры и участники (доступ к списанию) по project_id для фильтрации списка проектов."""
    normalized = [str(pid).strip() for pid in project_ids if str(pid).strip()]
    if not normalized:
        return {}, {}

    access_repo = UserProjectAccessRepository(session)
    access_by_project = await access_repo.list_access_by_project_ids(normalized)

    all_uids: set[int] = set()
    for uids in access_by_project.values():
        all_uids.update(int(u) for u in uids)

    positions: dict[int, str | None] = {}
    if all_uids:
        r = await session.execute(
            select(TimeTrackingUserModel.auth_user_id, TimeTrackingUserModel.position).where(
                TimeTrackingUserModel.auth_user_id.in_(sorted(all_uids))
            )
        )
        positions = {int(a): b for a, b in r.all()}

    auth_hints: dict[int, dict[str, str | None]] = {}
    if (authorization or "").strip():
        auth_hints = await fetch_auth_user_partner_hints_by_id(authorization or "")

    partners_by_project: dict[str, list[int]] = {}
    participants_by_project: dict[str, list[int]] = {}

    for pid in normalized:
        uids = access_by_project.get(pid, [])
        participants_by_project[pid] = sorted(set(int(u) for u in uids))
        partner_ids: list[int] = []
        for uid in uids:
            uid_i = int(uid)
            hint = auth_hints.get(uid_i) or {}
            if user_satisfies_partner_rule(
                positions.get(uid_i),
                hint.get("position"),
                hint.get("role"),
            ):
                partner_ids.append(uid_i)
        partners_by_project[pid] = sorted(set(partner_ids))

    return partners_by_project, participants_by_project
