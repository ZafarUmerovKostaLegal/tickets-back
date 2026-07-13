

from __future__ import annotations

import logging
import os
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from application.weekly_period import (
    is_work_week_edit_deadline_passed,
    local_today,
    previous_closed_saturday_fri_for_anchor,
    work_week_start_end_inclusive,
)
from infrastructure.repository_time_entry_unlocks import TimeEntryEditUnlockRepository
from infrastructure.repository_users import TimeTrackingUserRepository
from infrastructure.repository_weekly_submissions import WeeklySubmissionRepository

_log = logging.getLogger(__name__)


def _submit_tz() -> str:
    return (os.environ.get("WEEKLY_SUBMIT_TZ", "Asia/Tashkent") or "Asia/Tashkent").strip() or "Asia/Tashkent"


async def is_work_date_locked_for_user(
    session: AsyncSession,
    auth_user_id: int,
    work_date: date,
) -> bool:
    """Запись за work_date закрыта для правок сотрудником?

    До понедельника 12:00 (WEEKLY_SUBMIT_TZ, по умолчанию Asia/Tashkent) правки
    разрешены даже если неделя уже сдана. После дедлайна — закрыто
    (кроме временного manager unlock).
    """
    unlock_repo = TimeEntryEditUnlockRepository(session)
    if await unlock_repo.is_active_unlock(auth_user_id, work_date):
        return False

    return is_work_week_edit_deadline_passed(work_date, submit_tz=_submit_tz())


async def submit_reporting_week_for_user(
    session: AsyncSession,
    auth_user_id: int,
    *,
    anchor_date: date,
) -> tuple[date, date, bool]:
    """Сдать рабочую неделю (суббота–пятница), содержащую anchor_date. Возвращает (week_start, week_end, created)."""
    w0, w1 = work_week_start_end_inclusive(anchor_date)
    repo = WeeklySubmissionRepository(session)
    before = await repo.is_work_date_locked(auth_user_id, w0)
    await repo.upsert_submission(
        auth_user_id=auth_user_id,
        week_start=w0,
        week_end=w1,
        auto=False,
    )
    return w0, w1, not before


async def run_weekly_auto_submit(session: AsyncSession) -> int:

    anchor = local_today(_submit_tz())
    w0, w1 = previous_closed_saturday_fri_for_anchor(anchor)
    ur = TimeTrackingUserRepository(session)
    users = await ur.list_users()
    wr = WeeklySubmissionRepository(session)
    created = 0
    for u in users:
        if u.is_archived:
            continue
        before = await wr.is_work_date_locked(u.auth_user_id, w0)
        if before:
            continue
        await wr.upsert_submission(
            auth_user_id=u.auth_user_id,
            week_start=w0,
            week_end=w1,
            auto=True,
        )
        created += 1
        mgr = u.reports_to_auth_user_id
        _log.info(
            "weekly time submitted user=%s week=%s..%s manager=%s",
            u.auth_user_id,
            w0,
            w1,
            mgr,
        )
    return created


def run_weekly_auto_submit_sync() -> int:

    import asyncio

    from infrastructure.database import async_session_factory

    async def _go() -> int:
        async with async_session_factory() as session:
            n = await run_weekly_auto_submit(session)
            await session.commit()
            return n

    return asyncio.run(_go())
