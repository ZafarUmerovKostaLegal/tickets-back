

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from application.hourly_rate_logic import normalize_currency
from infrastructure.models import UserHourlyRateModel
from infrastructure.repositories import (
    ClientProjectRepository,
    HourlyRateRepository,
    UserProjectAccessRepository,
)


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v is not None and str(v).strip() else Decimal(0)


def is_per_project_billable_rate(billable_rate_type: str | None) -> bool:

    t = (billable_rate_type or "").strip().casefold()
    return t in {
        "project",
        "per_project",
        "by_project",
        "проект",
        "ставка_по_проекту",
        "project_rate",
        "project_billable_rate",
    }


def project_uses_shared_billable(project_row: Any) -> bool:
    if not project_row:
        return False
    if not is_per_project_billable_rate(getattr(project_row, "billable_rate_type", None)):
        return False
    amt = getattr(project_row, "project_billable_rate_amount", None)
    if amt is None:
        return False
    return _d(amt) > 0


async def delete_billable_rates_scoped_to_project(session: AsyncSession, project_id: str) -> None:

    pid = (project_id or "").strip()
    if not pid:
        return
    await session.execute(
        delete(UserHourlyRateModel).where(UserHourlyRateModel.applies_to_project_id == pid)
    )


def _shared_billable_config_changed(before: Any, after: Any) -> bool:
    if before is None or after is None:
        return True
    for field in (
        "billable_rate_type",
        "project_billable_rate_amount",
        "currency",
        "start_date",
        "end_date",
    ):
        if getattr(before, field, None) != getattr(after, field, None):
            return True
    return False


async def upsert_user_project_scoped_billable_rate(
    session: AsyncSession,
    *,
    auth_user_id: int,
    project_id: str,
    amount: Decimal,
    currency: str,
    valid_from: date | None,
    valid_to: date | None,
) -> None:

    pid = (project_id or "").strip()
    if not pid or amount <= 0:
        return
    hr = HourlyRateRepository(session)
    cur = normalize_currency(currency)
    existing = [
        r
        for r in await hr.list_by_user_and_kind(auth_user_id, "billable")
        if getattr(r, "applies_to_project_id", None) == pid
    ]
    if len(existing) > 1:
        keeper = min(existing, key=lambda r: r.id)
        for dup in existing:
            if dup.id != keeper.id:
                await hr.delete(auth_user_id, dup.id)
        existing = [keeper]
    if existing:
        row = min(existing, key=lambda r: r.id)
        await hr.update(
            auth_user_id=auth_user_id,
            rate_id=row.id,
            patch={"amount": amount, "currency": cur, "valid_from": valid_from, "valid_to": valid_to},
        )
        return
    await hr.create(
        auth_user_id=auth_user_id,
        rate_kind="billable",
        amount=amount,
        currency=cur,
        valid_from=valid_from,
        valid_to=valid_to,
        applies_to_project_id=pid,
    )


async def sync_project_billable_rates_to_assigned_users(
    session: AsyncSession,
    project_id: str,
) -> None:

    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(project_id)
    if not proj or not project_uses_shared_billable(proj):
        return
    par = UserProjectAccessRepository(session)
    uids = await par.list_auth_user_ids_for_project(project_id)
    amount = _d(proj.project_billable_rate_amount)
    if amount <= 0:
        return
    cur = normalize_currency(proj.currency)
    vf, vt = proj.start_date, proj.end_date
    for uid in uids:
        await upsert_user_project_scoped_billable_rate(
            session,
            auth_user_id=uid,
            project_id=project_id,
            amount=amount,
            currency=cur,
            valid_from=vf,
            valid_to=vt,
        )


async def reapply_project_billable_mode(
    session: AsyncSession,
    project_id: str,
    project_row: Any,
    *,
    project_row_before: Any | None = None,
) -> None:

    if not (project_id and str(project_id).strip()):
        return
    pid = str(project_id).strip()
    old_shared = project_row_before is not None and project_uses_shared_billable(project_row_before)
    new_shared = project_uses_shared_billable(project_row)
    if new_shared:
        if not old_shared or _shared_billable_config_changed(project_row_before, project_row):
            await sync_project_billable_rates_to_assigned_users(session, pid)
    elif old_shared:
        await delete_billable_rates_scoped_to_project(session, pid)
