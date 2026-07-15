from __future__ import annotations

from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

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
    batched = await list_report_auth_user_ids_for_project_periods(
        session,
        [(project_id, date_from, date_to)],
    )
    return batched.get(((project_id or "").strip(), date_from, date_to), set())


async def list_report_auth_user_ids_for_project_periods(
    session: AsyncSession,
    periods: list[tuple[str, date, date]],
) -> dict[tuple[str, date, date], set[int]]:
    """Один SQL по всем period-ключам вместо N distinct-запросов. Данные не меняет."""
    cleaned: list[tuple[str, date, date]] = []
    for project_id, date_from, date_to in periods:
        pid = (project_id or "").strip()
        if not pid or date_to < date_from:
            continue
        cleaned.append((pid, date_from, date_to))
    out: dict[tuple[str, date, date], set[int]] = {key: set() for key in cleaned}
    if not cleaned:
        return out

    pids = sorted({p for p, _, _ in cleaned})
    min_from = min(df for _, df, _ in cleaned)
    max_to = max(dt for _, _, dt in cleaned)
    q = (
        select(
            TimeEntryModel.project_id,
            TimeEntryModel.auth_user_id,
            TimeEntryModel.work_date,
        )
        .where(
            and_(
                TimeEntryModel.project_id.in_(pids),
                TimeEntryModel.work_date >= min_from,
                TimeEntryModel.work_date <= max_to,
                TimeEntryModel.voided_at.is_(None),
            )
        )
        .distinct()
    )
    rows = (await session.execute(q)).all()
    for project_id, auth_user_id, work_date in rows:
        if auth_user_id is None or work_date is None:
            continue
        pid = str(project_id)
        uid = int(auth_user_id)
        if uid <= 0:
            continue
        for key_pid, key_from, key_to in cleaned:
            if key_pid != pid:
                continue
            if key_from <= work_date <= key_to:
                out[(key_pid, key_from, key_to)].add(uid)
    return out


async def periods_overlapping_team_entries(
    session: AsyncSession,
    periods: list[tuple[str, date, date]],
    team_member_ids: set[int],
) -> set[tuple[str, date, date]]:
    """Периоды, в которых есть хотя бы одна запись времени от члена команды.

    Существенно легче полного distinct по всем auth_user_id проектов:
    фильтр по небольшой команде + index (project_id, work_date).
    """
    cleaned: list[tuple[str, date, date]] = []
    for project_id, date_from, date_to in periods:
        pid = (project_id or "").strip()
        if not pid or date_to < date_from:
            continue
        cleaned.append((pid, date_from, date_to))
    if not cleaned or not team_member_ids:
        return set()

    pids = sorted({p for p, _, _ in cleaned})
    min_from = min(df for _, df, _ in cleaned)
    max_to = max(dt for _, _, dt in cleaned)
    team = sorted({int(x) for x in team_member_ids if int(x) > 0})
    if not team:
        return set()

    q = (
        select(TimeEntryModel.project_id, TimeEntryModel.work_date)
        .where(
            and_(
                TimeEntryModel.project_id.in_(pids),
                TimeEntryModel.auth_user_id.in_(team),
                TimeEntryModel.work_date >= min_from,
                TimeEntryModel.work_date <= max_to,
                TimeEntryModel.voided_at.is_(None),
            )
        )
        .distinct()
    )
    hits: set[tuple[str, date]] = set()
    for project_id, work_date in (await session.execute(q)).all():
        if project_id is None or work_date is None:
            continue
        hits.add((str(project_id), work_date))

    out: set[tuple[str, date, date]] = set()
    for key_pid, key_from, key_to in cleaned:
        for wd_pid, wd in hits:
            if wd_pid != key_pid:
                continue
            if key_from <= wd <= key_to:
                out.add((key_pid, key_from, key_to))
                break
    return out


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
