

from __future__ import annotations

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


async def sync_project_billable_rates_to_assigned_users(
    session: AsyncSession,
    project_id: str,
) -> None:

    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(project_id)
    if not proj or not project_uses_shared_billable(proj):
        return
    par = UserProjectAccessRepository(session)
    hr = HourlyRateRepository(session)
    uids = await par.list_auth_user_ids_for_project(project_id)
    amount = _d(proj.project_billable_rate_amount)
    if amount <= 0:
        return
    cur = normalize_currency(proj.currency)
    vf, vt = proj.start_date, proj.end_date
    for uid in uids:
        existing = [
            r
            for r in await hr.list_by_user_and_kind(uid, "billable")
            if getattr(r, "applies_to_project_id", None) == project_id
        ]
        if existing:
            row = min(existing, key=lambda r: r.id)
            await hr.update(
                auth_user_id=uid,
                rate_id=row.id,
                patch={"amount": amount, "currency": cur, "valid_from": vf, "valid_to": vt},
            )
        else:
            await hr.create(
                auth_user_id=uid,
                rate_kind="billable",
                amount=amount,
                currency=cur,
                valid_from=vf,
                valid_to=vt,
                applies_to_project_id=project_id,
            )


async def reapply_project_billable_mode(
    session: AsyncSession,
    project_id: str,
    project_row: Any,
) -> None:

    if not (project_id and str(project_id).strip()):
        return
    pid = str(project_id).strip()
    if project_uses_shared_billable(project_row):
        await sync_project_billable_rates_to_assigned_users(session, pid)
    else:
        await delete_billable_rates_scoped_to_project(session, pid)
