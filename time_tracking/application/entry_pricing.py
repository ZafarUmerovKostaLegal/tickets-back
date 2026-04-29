

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from application.hourly_rate_logic import filter_rates_by_currency, pick_rate_for_date
from application.money_amounts import money_product_hours_rate


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal(0)


def _scoped_rates(
    user_rates: list[Any] | None,
    project_currency: str | None,
) -> list[Any] | None:
    if not user_rates:
        return None
    if not (project_currency and str(project_currency).strip()):
        return user_rates
    s = filter_rates_by_currency(user_rates, project_currency)
    return s


def billable_scoped_user_rates(
    user_rates: list[Any] | None,
    project_currency: str | None,
    time_entry_project_id: str | None,
) -> list[Any] | None:

    base = _scoped_rates(user_rates, project_currency)
    if not base:
        return None
    pid = (time_entry_project_id or "").strip()
    if not pid:
        return [r for r in base if not getattr(r, "applies_to_project_id", None)]
    proj_specific = [r for r in base if getattr(r, "applies_to_project_id", None) == pid]
    if proj_specific:
        return proj_specific
    return [r for r in base if not getattr(r, "applies_to_project_id", None)]


def _billable_rate_for_entry(
    work_date: date,
    user_rates: list[Any] | None,
    *,
    project_currency: str | None = None,
    time_entry_project_id: str | None = None,
) -> tuple[Decimal | None, str]:

    base_cur = (project_currency or "USD").strip()[:10] or "USD"
    scoped = billable_scoped_user_rates(user_rates, project_currency, time_entry_project_id)
    if not scoped:
        return None, base_cur
    rate = pick_rate_for_date(scoped, work_date)
    if not rate:
        return None, base_cur
    return _d(rate.amount), (rate.currency or base_cur).strip()[:10] or base_cur


def _billable_amount_for_entry(
    hours: Decimal,
    is_billable: bool,
    work_date: date,
    user_rates: list[Any] | None,
    *,
    project_currency: str | None = None,
    time_entry_project_id: str | None = None,
) -> tuple[Decimal, str]:

    out_cur = (project_currency or "USD").strip()[:10] or "USD"
    if not is_billable or not user_rates:
        return Decimal(0), out_cur
    scoped = billable_scoped_user_rates(user_rates, project_currency, time_entry_project_id)
    if not scoped:
        return Decimal(0), out_cur
    rate = pick_rate_for_date(scoped, work_date)
    if not rate:
        return Decimal(0), out_cur
    return money_product_hours_rate(hours, _d(rate.amount)), (rate.currency or out_cur).strip()[:10] or out_cur


def _cost_amount_for_entry(
    hours: Decimal,
    work_date: date,
    user_cost_rates: list[Any] | None,
    *,
    project_currency: str | None = None,
) -> tuple[Decimal, Decimal | None, str]:

    base_cur = (project_currency or "USD").strip()[:10] or "USD"
    scoped = _scoped_rates(user_cost_rates, project_currency)
    if not scoped:
        return Decimal(0), None, base_cur
    rate = pick_rate_for_date(scoped, work_date)
    if not rate:
        return Decimal(0), None, base_cur
    r_amt = _d(rate.amount)
    amt = money_product_hours_rate(hours, r_amt)
    return amt, r_amt, (rate.currency or base_cur).strip()[:10] or base_cur
