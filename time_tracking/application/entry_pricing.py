
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from application.hourly_rate_logic import filter_rates_by_currency, pick_rate_for_date
from application.money_amounts import money_product_hours_rate
from application.task_billing import flat_fee_for_task, is_flat_fee_task
from application.time_rounding import invoice_hours_for_billing, invoice_rate_for_billing


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal(0)


class _FallbackRate:
    """Ephemeral rate used when project shared amount applies or for tests."""

    __slots__ = ("amount", "currency", "applies_to_project_id", "valid_from", "valid_to", "id")

    def __init__(
        self,
        amount: Decimal,
        currency: str,
        *,
        applies_to_project_id: str | None = None,
        rate_id: str = "fallback",
    ) -> None:
        self.amount = amount
        self.currency = currency
        self.applies_to_project_id = applies_to_project_id
        self.valid_from = None
        self.valid_to = None
        self.id = rate_id


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


def shared_billable_amount_for_project(project_row: Any | None) -> Decimal | None:
    """Hourly amount from project settings when the project uses a shared billable rate."""
    if not project_row:
        return None
    from application.project_billable_rate_sync import project_uses_shared_billable

    if not project_uses_shared_billable(project_row):
        return None
    amt = _d(getattr(project_row, "project_billable_rate_amount", None))
    return amt if amt > 0 else None


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
    global_rates = [r for r in base if not getattr(r, "applies_to_project_id", None)]
    if global_rates:
        return global_rates
    # Same legacy fallback as pick_billable_rate_for_entry: only other-project rates exist.
    other = [r for r in base if getattr(r, "applies_to_project_id", None)]
    return other or None


def pick_billable_rate_for_entry(
    work_date: date,
    user_rates: list[Any] | None,
    *,
    project_currency: str | None = None,
    time_entry_project_id: str | None = None,
    shared_fallback_amount: Decimal | None = None,
    project_row: Any | None = None,
) -> Any | None:
    """Ставка billable на дату записи.

    Порядок:
    1) ставка, привязанная к этому проекту, на дату;
    2) общая (без проекта) на дату;
    3) сумма shared-ставки проекта (если режим «ставка по проекту»);
    4) любая другая проектная ставка в валюте проекта на дату
       (legacy: при онбординге часто создают только project-scoped ставки).
    """
    base = _scoped_rates(user_rates, project_currency) or []
    pid = (time_entry_project_id or "").strip()
    out_cur = (project_currency or "USD").strip()[:10] or "USD"
    global_rates = [r for r in base if not getattr(r, "applies_to_project_id", None)]
    if pid:
        proj_specific = [r for r in base if getattr(r, "applies_to_project_id", None) == pid]
        if proj_specific:
            rate = pick_rate_for_date(proj_specific, work_date)
            if rate is not None:
                return rate
    if global_rates:
        rate = pick_rate_for_date(global_rates, work_date)
        if rate is not None:
            return rate

    fb = shared_fallback_amount
    if fb is None:
        fb = shared_billable_amount_for_project(project_row)
    fb_amt = _d(fb) if fb is not None else Decimal(0)
    if fb_amt > 0:
        return _FallbackRate(
            fb_amt,
            out_cur,
            applies_to_project_id=pid or None,
            rate_id=f"shared:{pid or 'project'}",
        )

    other = [
        r
        for r in base
        if getattr(r, "applies_to_project_id", None)
        and (not pid or getattr(r, "applies_to_project_id", None) != pid)
    ]
    return pick_rate_for_date(other, work_date) if other else None


def _billable_rate_for_entry(
    work_date: date,
    user_rates: list[Any] | None,
    *,
    project_currency: str | None = None,
    time_entry_project_id: str | None = None,
    task: Any | None = None,
    shared_fallback_amount: Decimal | None = None,
    project_row: Any | None = None,
) -> tuple[Decimal | None, str]:

    base_cur = (project_currency or "USD").strip()[:10] or "USD"
    ff = flat_fee_for_task(task)
    if ff is not None:
        amt, cur = ff
        return amt, cur
    rate = pick_billable_rate_for_entry(
        work_date,
        user_rates,
        project_currency=project_currency,
        time_entry_project_id=time_entry_project_id,
        shared_fallback_amount=shared_fallback_amount,
        project_row=project_row,
    )
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
    task: Any | None = None,
    shared_fallback_amount: Decimal | None = None,
    project_row: Any | None = None,
) -> tuple[Decimal, str]:

    out_cur = (project_currency or "USD").strip()[:10] or "USD"
    ff = flat_fee_for_task(task)
    if ff is not None:
        amt, cur = ff
        if not is_billable:
            return Decimal(0), cur
        return amt, cur
    if not is_billable:
        return Decimal(0), out_cur
    rate = pick_billable_rate_for_entry(
        work_date,
        user_rates,
        project_currency=project_currency,
        time_entry_project_id=time_entry_project_id,
        shared_fallback_amount=shared_fallback_amount,
        project_row=project_row,
    )
    if not rate:
        return Decimal(0), out_cur
    # Same as invoice / partner Excel: minute-round hours → 2dp, rate 2dp, then money product.
    qty = invoice_hours_for_billing(hours)
    if qty <= 0:
        return Decimal(0), out_cur
    unit = invoice_rate_for_billing(rate.amount)
    if unit <= 0:
        return Decimal(0), out_cur
    return money_product_hours_rate(qty, unit), (rate.currency or out_cur).strip()[:10] or out_cur


def billable_amount_respecting_package(
    hours: Decimal,
    is_billable: bool,
    work_date: date,
    user_rates: list[Any] | None,
    *,
    project_currency: str | None = None,
    time_entry_project_id: str | None = None,
    task: Any | None = None,
    package_split: Any | None = None,
    shared_fallback_amount: Decimal | None = None,
    project_row: Any | None = None,
) -> tuple[Decimal, str]:
    """Flat-fee tasks always bill the fixed amount; hour-package overage applies only to hourly tasks."""
    if is_flat_fee_task(task):
        return _billable_amount_for_entry(
            hours,
            is_billable,
            work_date,
            user_rates,
            project_currency=project_currency,
            time_entry_project_id=time_entry_project_id,
            task=task,
            shared_fallback_amount=shared_fallback_amount,
            project_row=project_row,
        )
    if package_split is not None:
        oh = _d(getattr(package_split, "overage_hours", 0))
        return _billable_amount_for_entry(
            oh,
            bool(is_billable) and oh > 0,
            work_date,
            user_rates,
            project_currency=project_currency,
            time_entry_project_id=time_entry_project_id,
            task=task,
            shared_fallback_amount=shared_fallback_amount,
            project_row=project_row,
        )
    return _billable_amount_for_entry(
        hours,
        is_billable,
        work_date,
        user_rates,
        project_currency=project_currency,
        time_entry_project_id=time_entry_project_id,
        task=task,
        shared_fallback_amount=shared_fallback_amount,
        project_row=project_row,
    )


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
    qty = invoice_hours_for_billing(hours)
    r_amt = invoice_rate_for_billing(rate.amount)
    if qty <= 0 or r_amt <= 0:
        return Decimal(0), r_amt if r_amt > 0 else _d(rate.amount), (rate.currency or base_cur).strip()[:10] or base_cur
    amt = money_product_hours_rate(qty, r_amt)
    return amt, r_amt, (rate.currency or base_cur).strip()[:10] or base_cur
