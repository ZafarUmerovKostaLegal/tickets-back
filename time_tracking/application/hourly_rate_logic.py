

from __future__ import annotations

from datetime import date
from typing import Any

_MIN = date(1, 1, 1)
_MAX = date(9999, 12, 31)


def effective_start(d: date | None) -> date:
    return d if d is not None else _MIN


def effective_end(d: date | None) -> date:
    return d if d is not None else _MAX


def intervals_overlap(
    a_start: date | None,
    a_end: date | None,
    b_start: date | None,
    b_end: date | None,
) -> bool:

    if effective_end(a_end) < effective_start(b_start):
        return False
    if effective_end(b_end) < effective_start(a_start):
        return False
    return True


def validate_range_order(valid_from: date | None, valid_to: date | None) -> None:
    if valid_from is not None and valid_to is not None and valid_from > valid_to:
        raise ValueError("Дата начала не может быть позже даты окончания")


def normalize_currency(currency: str | None) -> str:
    return (currency or "USD").strip().upper()[:10] or "USD"


def filter_rates_by_currency(rows: list[Any], currency: str) -> list[Any]:

    cur = normalize_currency(currency)
    return [r for r in rows if normalize_currency(getattr(r, "currency", None)) == cur]


def pick_rate_for_date(
    rows: list[Any],
    on_date: date,
    *,
    valid_from_attr: str = "valid_from",
    valid_to_attr: str = "valid_to",
) -> Any | None:

    candidates: list[Any] = []
    for row in rows:
        vf = getattr(row, valid_from_attr, None)
        vt = getattr(row, valid_to_attr, None)
        if effective_start(vf) <= on_date <= effective_end(vt):
            candidates.append(row)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    return sorted(candidates, key=lambda r: getattr(r, "id", ""))[0]
