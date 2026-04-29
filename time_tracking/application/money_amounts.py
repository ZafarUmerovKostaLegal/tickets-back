

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

_Q2 = Decimal("0.01")


def money_product_hours_rate(hours: Decimal, rate_per_hour: Decimal) -> Decimal:

    return (hours * rate_per_hour).quantize(_Q2, rounding=ROUND_HALF_UP)
