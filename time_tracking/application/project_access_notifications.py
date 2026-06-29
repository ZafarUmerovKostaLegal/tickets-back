from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from application.manual_tt_users import MANUAL_TT_USER_EMAIL_DOMAIN
from infrastructure.config import Settings, get_settings
from infrastructure.project_access_mail import send_project_access_added_email
from infrastructure.repositories import ClientProjectRepository, ClientRepository, TimeTrackingUserRepository

_log = logging.getLogger(__name__)
_TIMEOUT_SEC = 60.0


def _is_deliverable_email(email: str | None) -> bool:
    addr = (email or "").strip().lower()
    if not addr or "@" not in addr:
        return False
    if addr.endswith(f"@{MANUAL_TT_USER_EMAIL_DOMAIN}"):
        return False
    return True


async def _notify_user_added_to_projects(
    session: AsyncSession,
    settings: Settings,
    *,
    auth_user_id: int,
    project_ids: list[str],
) -> None:
    if not settings.notify_project_access_added:
        return
    pids = [(p or "").strip() for p in project_ids if (p or "").strip()]
    if not pids:
        return

    user = await TimeTrackingUserRepository(session).get_by_auth_user_id(auth_user_id)
    if user is None:
        _log.warning("project access notify: пользователь TT не найден auth_user_id=%s", auth_user_id)
        return
    if not _is_deliverable_email(user.email):
        _log.info(
            "project access notify: пропуск — нет рабочего email auth_user_id=%s email=%s",
            auth_user_id,
            user.email,
        )
        return

    projects_repo = ClientProjectRepository(session)
    clients_repo = ClientRepository(session)
    to_email = user.email.strip()

    for pid in pids:
        proj = await projects_repo.get_by_id_global(pid)
        if proj is None:
            _log.warning("project access notify: проект не найден project_id=%s", pid)
            continue
        client_name = "—"
        if proj.client_id:
            client = await clients_repo.get_by_id(proj.client_id)
            if client is not None and (client.name or "").strip():
                client_name = client.name.strip()
        await send_project_access_added_email(
            settings,
            to_email=to_email,
            project_name=(proj.name or "").strip() or "—",
            client_name=client_name,
        )


async def run_project_access_added_notifications_safe(
    session: AsyncSession,
    *,
    auth_user_id: int,
    project_ids: list[str],
    settings: Settings | None = None,
) -> None:
    cfg = settings or get_settings()
    try:
        await asyncio.wait_for(
            _notify_user_added_to_projects(
                session,
                cfg,
                auth_user_id=auth_user_id,
                project_ids=project_ids,
            ),
            timeout=_TIMEOUT_SEC,
        )
    except TimeoutError:
        _log.error(
            "project access notify: таймаут auth_user_id=%s projects=%s",
            auth_user_id,
            project_ids,
        )
    except Exception as e:
        _log.error(
            "project access notify: ошибка auth_user_id=%s projects=%s: %s: %s",
            auth_user_id,
            project_ids,
            type(e).__name__,
            e,
        )
