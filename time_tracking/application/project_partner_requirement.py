

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from application.auth_user_directory import fetch_auth_user_partner_hints_by_id
from infrastructure.models import TimeTrackingUserModel
from infrastructure.repository_access import UserProjectAccessRepository
from infrastructure.repository_clients import ClientProjectRepository


def job_title_indicates_partner(position: str | None) -> bool:

    if not position or not str(position).strip():
        return False
    s = str(position).strip().casefold()
    if "партн" in s:
        return True
    if "partner" in s:
        return True
    return False


def org_role_indicates_partner(role: str | None) -> bool:

    if not role or not str(role).strip():
        return False
    s = str(role).strip().casefold()
    if "партн" in s:
        return True
    if "partner" in s:
        return True
    return False


def merged_position_tt_auth(tt_position: str | None, auth_position: str | None) -> str | None:

    if tt_position is not None and str(tt_position).strip():
        return str(tt_position).strip()
    if auth_position is not None and str(auth_position).strip():
        return str(auth_position).strip()
    return None


def user_satisfies_partner_rule(
    tt_position: str | None,
    auth_position: str | None,
    auth_org_role: str | None,
) -> bool:
    m = merged_position_tt_auth(tt_position, auth_position)
    if job_title_indicates_partner(m):
        return True
    return org_role_indicates_partner(auth_org_role)


async def ensure_projects_have_partner_assignee(
    session: AsyncSession,
    access_repo: UserProjectAccessRepository,
    project_ids: set[str],
    *,
    projects: ClientProjectRepository | None = None,
    authorization: str | None = None,
) -> None:

    auth_hints: dict[int, dict[str, str | None]] = {}
    if (authorization or "").strip():
        auth_hints = await fetch_auth_user_partner_hints_by_id(authorization or "")

    for pid in project_ids:
        if not (pid and str(pid).strip()):
            continue
        p = str(pid).strip()
        uids = await access_repo.list_auth_user_ids_for_project(p)
        if not uids:
            continue
        r = await session.execute(
            select(TimeTrackingUserModel.auth_user_id, TimeTrackingUserModel.position).where(
                TimeTrackingUserModel.auth_user_id.in_(uids)
            )
        )
        by_uid: dict[int, str | None] = {int(a): b for a, b in r.all()}
        ok = False
        for uid in uids:
            uid_i = int(uid)
            hint = auth_hints.get(uid_i) or {}
            ap = hint.get("position")
            ar = hint.get("role")
            if user_satisfies_partner_rule(by_uid.get(uid_i), ap, ar):
                ok = True
                break
        if ok:
            continue
        label = p
        if projects is not None:
            pr = await projects.get_by_id_global(p)
            if pr is not None and getattr(pr, "name", None):
                label = str(pr.name).strip() or p
        raise ValueError(
            f"По проекту «{label}» среди пользователей с доступом к списанию времени "
            f"нужен хотя бы один партнёр: должность с «партн…»/Partner в position "
            f"(в учёте времени или в профиле auth) и/или соответствующая орг-роль. "
            f"Синхронизируйте пользователя (POST /users) с должностью или укажите партнёра в org.",
        )
