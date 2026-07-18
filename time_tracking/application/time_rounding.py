

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal


_HOURS_QUANT = Decimal("0.000001")
_SECONDS_PER_HOUR = Decimal(3600)


def seconds_from_hours(hours: Decimal | float | int | str) -> int:

    h = hours if isinstance(hours, Decimal) else Decimal(str(hours))
    seconds = (h * _SECONDS_PER_HOUR).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(seconds)


def hours_from_seconds(seconds: int) -> Decimal:

    h = (Decimal(int(seconds)) / _SECONDS_PER_HOUR).quantize(_HOURS_QUANT, rounding=ROUND_HALF_UP)
    return h


def resolve_duration_for_entry(duration_seconds: int | None, hours: Decimal | None) -> int:

    if duration_seconds is not None:
        sec = int(duration_seconds)
    elif hours is not None:
        h = hours if isinstance(hours, Decimal) else Decimal(str(hours))
        sec = seconds_from_hours(h)
    else:
        raise ValueError("Не указана длительность (durationSeconds или hours)")
    sec = quantize_seconds_to_minute(sec)
    if sec <= 0:
        raise ValueError("Длительность должна быть не меньше 1 минуты")
    return sec


def quantize_seconds_to_minute(seconds: int) -> int:

    s = int(seconds)
    if s <= 0:
        return 0
    minutes = (Decimal(s) / Decimal(60)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minutes) * 60


def round_decimal_hours_to_minute(hours: Decimal | float | int | str) -> Decimal:
    """Match FE `roundDecimalHoursToMinute`: Math.round(h * 60) / 60."""
    h = hours if isinstance(hours, Decimal) else Decimal(str(hours or 0))
    if h <= 0:
        return Decimal(0)
    minutes = (h * Decimal(60)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (minutes / Decimal(60)).quantize(_HOURS_QUANT, rounding=ROUND_HALF_UP)


_HOURS_MONEY_QUANT = Decimal("0.01")


def invoice_hours_for_billing(hours: Decimal | float | int | str) -> Decimal:
    """Hours for invoice / partner Excel lines: minute-round then 2dp (FE `excelNum2`)."""
    h = round_decimal_hours_to_minute(hours)
    if h <= 0:
        return Decimal(0)
    return h.quantize(_HOURS_MONEY_QUANT, rounding=ROUND_HALF_UP)


def invoice_rate_for_billing(rate: Decimal | float | int | str | None) -> Decimal:
    """Match FE Excel rate cell: Math.round(rate * 100) / 100."""
    if rate is None:
        return Decimal(0)
    r = rate if isinstance(rate, Decimal) else Decimal(str(rate or 0))
    if r <= 0:
        return Decimal(0)
    return r.quantize(_HOURS_MONEY_QUANT, rounding=ROUND_HALF_UP)
