

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
