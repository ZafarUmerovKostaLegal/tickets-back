

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

_Q2 = Decimal("0.01")
_Q6 = Decimal("0.000001")
_ZERO = Decimal(0)


def _d(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    return Decimal(str(v)) if v else Decimal(0)


def _hours(v: Decimal) -> float:
    return float(v.quantize(_Q6, rounding=ROUND_HALF_UP))


def _money(v: Decimal) -> float:
    return float(v.quantize(_Q2, rounding=ROUND_HALF_UP))


def _percent_billable(total: Decimal, billable: Decimal) -> float:

    t = _d(total)
    if t <= 0:
        return 0.0
    p = (_d(billable) / t) * Decimal(100)
    return float(p.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def build_response(
    results: list[dict],
    total_entries: int,
    page: int,
    per_page: int,
    report_type: str,
    group_by: str | None,
    date_from: date,
    date_to: date,
) -> dict:

    if total_entries > 0:
        total_pages = (total_entries + per_page - 1) // per_page
    else:
        total_pages = 1

    return {
        "results": results,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
            "total_entries": total_entries,
            "next_page": page + 1 if page < total_pages else None,
            "previous_page": page - 1 if page > 1 else None,
        },
        "meta": {
            "report_type": report_type,
            "group_by": group_by,
            "from": date_from.isoformat(),
            "to": date_to.isoformat(),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
