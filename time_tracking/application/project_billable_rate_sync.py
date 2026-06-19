

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
        b_val = getattr(before, field, None)
        a_val = getattr(after, field, None)
        if field == "project_billable_rate_amount":
            if _d(b_val) != _d(a_val):
                return True
            continue
        if field == "currency":
            if normalize_currency(b_val) != normalize_currency(a_val):
                return True
            continue
        if b_val != a_val:
            return True
    return False


_BUDGET_ONLY_PATCH_KEYS = frozenset(
    {
        "budget_hours",
        "budget_amount",
        "progress_budget_amount",
        "budget_type",
        "budget_resets_every_month",
        "budget_includes_expenses",
        "send_budget_alerts",
        "budget_alert_threshold_percent",
        "fixed_fee_amount",
    }
)


def patch_only_budget_fields(patch: dict[str, Any] | None) -> bool:
    if not patch:
        return False
    keys = set(patch.keys())
    return bool(keys) and keys <= _BUDGET_ONLY_PATCH_KEYS


async def _delete_user_project_scoped_billable_rates(
    session: AsyncSession,
    auth_user_id: int,
    project_id: str,
) -> None:
    pid = (project_id or "").strip()
    if not pid:
        return
    hr = HourlyRateRepository(session)
    for row in await hr.list_by_user_and_kind(auth_user_id, "billable"):
        if getattr(row, "applies_to_project_id", None) == pid:
            await hr.delete(auth_user_id, row.id)


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
        try:
            await hr.update(
                auth_user_id=auth_user_id,
                rate_id=row.id,
                patch={"amount": amount, "currency": cur, "valid_from": valid_from, "valid_to": valid_to},
            )
            return
        except ValueError:
            await _delete_user_project_scoped_billable_rates(session, auth_user_id, pid)
    try:
        await hr.create(
            auth_user_id=auth_user_id,
            rate_kind="billable",
            amount=amount,
            currency=cur,
            valid_from=valid_from,
            valid_to=valid_to,
            applies_to_project_id=pid,
        )
    except ValueError:
        await _delete_user_project_scoped_billable_rates(session, auth_user_id, pid)
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
    for uid in uids:
        await upsert_user_project_scoped_billable_rate(
            session,
            auth_user_id=uid,
            project_id=project_id,
            amount=amount,
            currency=cur,
            valid_from=None,
            valid_to=None,
        )


async def reapply_project_billable_mode(
    session: AsyncSession,
    project_id: str,
    project_row: Any,
    *,
    project_row_before: Any | None = None,
    patch: dict[str, Any] | None = None,
) -> None:

    if not (project_id and str(project_id).strip()):
        return
    if patch_only_budget_fields(patch):
        return
    pid = str(project_id).strip()
    old_shared = project_row_before is not None and project_uses_shared_billable(project_row_before)
    new_shared = project_uses_shared_billable(project_row)
    if new_shared:
        if not old_shared or _shared_billable_config_changed(project_row_before, project_row):
            await sync_project_billable_rates_to_assigned_users(session, pid)
    elif old_shared:
        await delete_billable_rates_scoped_to_project(session, pid)
