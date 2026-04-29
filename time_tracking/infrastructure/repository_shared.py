from __future__ import annotations

from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


_ENTRY_HOURS_QUANT = Decimal("0.000001")


def normalize_time_entry_hours(hours: Decimal) -> Decimal:

    q = hours.quantize(_ENTRY_HOURS_QUANT, rounding=ROUND_HALF_UP)
    if q <= 0:
        raise ValueError("Количество часов должно быть больше нуля")
    return q


_REPORT_VISIBILITY = frozenset({"managers_only", "all_assigned"})
_PROJECT_TYPES = frozenset({"time_and_materials", "fixed_fee", "non_billable"})


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _strip_opt(v: str | None) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _decimal_none(v: Any) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))


def _to_decimal(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v))
