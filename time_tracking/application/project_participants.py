from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from application.entry_pricing import billable_scoped_user_rates
from application.hourly_rate_logic import pick_rate_for_date
from application.project_billable_rate_sync import project_uses_shared_billable
from application.report_builder import _load_user_rates
from infrastructure.repositories import (
    ClientProjectRepository,
    TimeEntryRepository,
    TimeTrackingUserRepository,
    UserProjectAccessRepository,
)


def _d(v: object) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v is not None and str(v).strip() else Decimal(0)


async def list_project_participants_with_rates(
    session: AsyncSession,
    *,
    project_id: str,
    as_of: date | None = None,
) -> dict | None:
    pid = (project_id or "").strip()
    if not pid:
        return None

    cpr = ClientProjectRepository(session)
    proj = await cpr.get_by_id_global(pid)
    if not proj:
        return None

    access_repo = UserProjectAccessRepository(session)
    entry_repo = TimeEntryRepository(session)
    user_repo = TimeTrackingUserRepository(session)

    from_access = set(await access_repo.list_auth_user_ids_for_project(pid))
    from_entries = set(
        await entry_repo.list_auth_users_with_entries_on_project(
            date(2000, 1, 1),
            date.today(),
            pid,
        )
    )
    member_ids = sorted(from_access | from_entries)
    users = await user_repo.list_by_auth_user_ids(member_ids)
    by_uid = {u.auth_user_id: u for u in users}
    rates_map = await _load_user_rates(session, member_ids or None)

    project_currency = (getattr(proj, "currency", None) or "USD").strip()[:10] or "USD"
    on_date = as_of or date.today()
    uses_shared = project_uses_shared_billable(proj)
    shared_amount = _d(getattr(proj, "project_billable_rate_amount", None))

    participants: list[dict] = []
    for uid in member_ids:
        u = by_uid.get(uid)
        user_rates = rates_map.get(uid) or []
        scoped = billable_scoped_user_rates(user_rates, project_currency, pid) or []
        rate_row = pick_rate_for_date(scoped, on_date) if scoped else None
        billable_amount = _d(getattr(rate_row, "amount", None)) if rate_row else None
        has_rate = billable_amount is not None and billable_amount > 0
        if not has_rate and uses_shared and uid in from_access and shared_amount > 0:
            billable_amount = shared_amount
            has_rate = True

        participants.append(
            {
                "auth_user_id": uid,
                "display_name": (u.display_name if u else None),
                "email": (u.email if u else None),
                "position": ((u.position or "").strip() or None) if u else None,
                "is_archived": bool(u.is_archived) if u else False,
                "is_blocked": bool(u.is_blocked) if u else False,
                "has_project_access": uid in from_access,
                "has_time_entries": uid in from_entries,
                "has_billable_rate": has_rate,
                "billable_hourly_amount": float(billable_amount) if has_rate and billable_amount else None,
                "billable_rate_currency": project_currency if has_rate else None,
            }
        )

    return {
        "project_id": pid,
        "client_id": str(proj.client_id),
        "currency": project_currency,
        "uses_shared_billable_rate": uses_shared,
        "shared_billable_hourly_amount": float(shared_amount) if uses_shared and shared_amount > 0 else None,
        "participants": participants,
    }
