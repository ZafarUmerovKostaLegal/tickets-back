

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from application.hourly_rate_logic import normalize_currency, pick_rate_for_date
from infrastructure.models import UserHourlyRateModel
from infrastructure.report_cache import invalidate_all_reports
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
    invalidate_all_reports()


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
    """Upsert project-scoped billable rate without wiping dated history.

    When the caller passes open dates (None/None) — typical for project settings —
    update amount/currency on the *currently effective* project rate and keep its
    valid_from/valid_to. Creating a brand-new open interval only when none exist.
    """

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

    # Collapse only true open-interval duplicates (both ends null), not dated history.
    open_dups = [r for r in existing if r.valid_from is None and r.valid_to is None]
    if len(open_dups) > 1:
        keeper = min(open_dups, key=lambda r: r.id)
        for dup in open_dups:
            if dup.id != keeper.id:
                await hr.delete(auth_user_id, dup.id)
        existing = [
            r
            for r in await hr.list_by_user_and_kind(auth_user_id, "billable")
            if getattr(r, "applies_to_project_id", None) == pid
        ]

    today = date.today()
    target = pick_rate_for_date(existing, today)
    if target is None:
        # Prefer an open-ended future/current row over inventing a second open interval.
        open_ended = [r for r in existing if r.valid_to is None]
        if open_ended:
            target = max(
                open_ended,
                key=lambda r: (
                    r.valid_from or date.min,
                    r.id,
                ),
            )

    if target is not None:
        patch: dict[str, Any] = {"amount": amount, "currency": cur}
        # Only rewrite interval bounds when the caller supplies at least one bound.
        # Project-access saves pass None/None and must not clear "change from date" history.
        if valid_from is not None or valid_to is not None:
            patch["valid_from"] = valid_from
            patch["valid_to"] = valid_to
        try:
            await hr.update(
                auth_user_id=auth_user_id,
                rate_id=target.id,
                patch=patch,
            )
            return
        except ValueError:
            # Fall through to create only if update failed due to overlap constraints.
            pass

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
        # Last resort: remove open-interval dups only, then recreate one open row.
        for r in list(existing):
            if r.valid_from is None and r.valid_to is None:
                await hr.delete(auth_user_id, r.id)
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
