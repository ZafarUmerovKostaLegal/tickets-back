

from __future__ import annotations

from decimal import Decimal
from typing import Any

from application.services.reports._base import _d, _ZERO


def effective_budget_amount(p: Any) -> Decimal:

    v = getattr(p, "budget_amount", None)
    if v is not None and _d(v) > 0:
        return _d(v)
    if getattr(p, "project_type", None) == "fixed_fee":
        f = getattr(p, "fixed_fee_amount", None)
        if f is not None and _d(f) > 0:
            return _d(f)
    return _ZERO


def budget_mode(p: Any) -> str:

    h = p.budget_hours is not None and _d(p.budget_hours) > 0
    m = effective_budget_amount(p) > 0
    if h and m:
        return "hours_and_money"
    if h:
        return "hours"
    if m:
        return "money"
    return "none"


def normalize_budget_type_for_persist(
    budget_hours: Decimal | None,
    budget_amount: Decimal | None,
) -> str | None:

    h = budget_hours is not None and _d(budget_hours) > 0
    m = budget_amount is not None and _d(budget_amount) > 0
    if h and m:
        return "hours_and_money"
    if h:
        return "hours"
    if m:
        return "money"
    return None


def budget_limit_hours(p: Any) -> Decimal:
    v = p.budget_hours
    if v is None or _d(v) <= 0:
        return _ZERO
    return _d(v)


def budget_limit_money(p: Any) -> Decimal:
    return effective_budget_amount(p)
