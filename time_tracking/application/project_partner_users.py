from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.auth_user_directory import fetch_auth_user_partner_hints_by_id
from application.project_partner_requirement import user_satisfies_partner_rule
from infrastructure.auth_directory_cache import get_partners_by_projects, set_partners_by_projects
from infrastructure.models import TimeTrackingUserModel
from infrastructure.repository_access import UserProjectAccessRepository


async def list_partner_auth_user_ids_for_project(
    session: AsyncSession,
    access_repo: UserProjectAccessRepository,
    project_id: str,
    *,
    authorization: str | None = None,
) -> list[int]:
    by_project = await list_partner_auth_user_ids_by_projects(
        session,
        access_repo,
        [project_id],
        authorization=authorization,
    )
    return by_project.get((project_id or "").strip(), [])


async def list_partner_auth_user_ids_by_projects(
    session: AsyncSession,
    access_repo: UserProjectAccessRepository,
    project_ids: list[str],
    *,
    authorization: str | None = None,
) -> dict[str, list[int]]:
    """Партнёры по нескольким проектам за 1 проход (без N+1 auth/users + access)."""
    pids = sorted({str(p).strip() for p in project_ids if str(p).strip()})
    if not pids:
        return {}
    cached = get_partners_by_projects(pids, authorization)
    if cached is not None:
        return cached
    access_by_project = await access_repo.list_access_by_project_ids(pids)
    all_uids: set[int] = set()
    for uids in access_by_project.values():
        all_uids.update(int(u) for u in uids)
    auth_hints: dict[int, dict[str, str | None]] = {}
    if all_uids and (authorization or "").strip():
        auth_hints = await fetch_auth_user_partner_hints_by_id(authorization or "")
    by_uid: dict[int, str | None] = {}
    if all_uids:
        r = await session.execute(
            select(TimeTrackingUserModel.auth_user_id, TimeTrackingUserModel.position).where(
                TimeTrackingUserModel.auth_user_id.in_(sorted(all_uids))
            )
        )
        by_uid = {int(a): b for a, b in r.all()}
    out: dict[str, list[int]] = {}
    for pid in pids:
        partners: list[int] = []
        for uid in access_by_project.get(pid, []):
            uid_i = int(uid)
            hint = auth_hints.get(uid_i) or {}
            if user_satisfies_partner_rule(
                by_uid.get(uid_i),
                hint.get("position"),
                hint.get("role"),
            ):
                partners.append(uid_i)
        out[pid] = sorted(set(partners))
    set_partners_by_projects(pids, authorization, out)
    return out
