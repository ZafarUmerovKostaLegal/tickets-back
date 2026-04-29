

from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from application.hourly_rate_logic import filter_rates_by_currency, pick_rate_for_date
from application.project_billable_rate_sync import project_uses_shared_billable
from infrastructure.repositories import ClientProjectRepository, HourlyRateRepository


async def validate_hourly_rates_for_project_access(
    session: AsyncSession,
    *,
    auth_user_id: int,
    project_ids: list[str],
) -> None:

    if not project_ids:
        return
    cpr = ClientProjectRepository(session)
    hr = HourlyRateRepository(session)
    on_date = date.today()
    for pid in project_ids:
        row = await cpr.get_by_id_global(pid)
        if not row:
            continue
        cur = (row.currency or "USD").strip().upper()[:10] or "USD"
        if project_uses_shared_billable(row):
            continue
        rates = await hr.list_by_user_and_kind(auth_user_id, "billable")
        scoped = filter_rates_by_currency(rates, cur)
        if pick_rate_for_date(scoped, on_date) is None:
            raise ValueError(
                f"Нельзя выдать доступ к проекту «{row.name}» (валюта {cur}): у пользователя нет "
                f"почасовой ставки «оплачиваемая (billable)» в валюте {cur} на текущую дату. "
                "Добавьте ставку в разделе почасовых ставок пользователя, затем повторите назначение."
            )
