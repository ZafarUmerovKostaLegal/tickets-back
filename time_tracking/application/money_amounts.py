from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

_Q2 = Decimal("0.01")


def to_decimal(v: Any) -> Decimal:
    """Coerce DB/JSON numbers to Decimal. Numeric drivers often return int for whole values."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, bool) or v is None:
        return Decimal(0)
    s = str(v).strip()
    if not s:
        return Decimal(0)
    return Decimal(s)


def money_product_hours_rate(hours: Decimal, rate_per_hour: Decimal) -> Decimal:

    return (to_decimal(hours) * to_decimal(rate_per_hour)).quantize(_Q2, rounding=ROUND_HALF_UP)
