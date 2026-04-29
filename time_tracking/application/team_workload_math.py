

from datetime import date
from decimal import Decimal


def period_days_inclusive(date_from: date, date_to: date) -> int:
    if date_to < date_from:
        raise ValueError("date_to не может быть раньше date_from")
    return (date_to - date_from).days + 1


def capacity_for_period(weekly_capacity_hours: Decimal, date_from: date, date_to: date) -> Decimal:
    days = Decimal(period_days_inclusive(date_from, date_to))
    return weekly_capacity_hours * days / Decimal("7")


def workload_percent(part: Decimal, whole: Decimal) -> int:
    if whole <= 0:
        return 0
    p = float(part / whole * Decimal("100"))
    return min(100, max(0, round(p)))
