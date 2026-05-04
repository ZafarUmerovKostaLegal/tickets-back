"""Демо-бюджеты для сидов TT (дашборд проекта / списки без «бюджет не задан»)."""

from __future__ import annotations

import random
from decimal import Decimal

from application.budget_mode import normalize_budget_type_for_persist


def demo_budget_fields_for_project(
    project_currency: str,
    rnd: random.Random,
    *,
    slot: int,
) -> dict[str, object]:
    cur = (project_currency or "USD").strip().upper()[:12]
    r = slot % 7

    def money_amount() -> Decimal:
        if cur == "UZS":
            lo, hi = 45_000_000, 180_000_000
        elif cur in ("USD", "EUR"):
            lo, hi = 18_000, 95_000
        else:
            lo, hi = 12_000, 80_000
        return Decimal(str(rnd.randint(lo, hi)))

    bh: Decimal | None = None
    ba: Decimal | None = None

    if r == 0:
        bh = Decimal(str(rnd.randint(450, 1400)))
        ba = money_amount()
    elif r == 6:
        bh = Decimal(str(rnd.randint(600, 1800)))
    else:
        ba = money_amount()

    bt = normalize_budget_type_for_persist(bh, ba)
    return {
        "budget_type": bt,
        "budget_amount": ba,
        "progress_budget_amount": None,
        "budget_hours": bh,
    }
