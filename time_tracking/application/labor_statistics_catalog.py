from __future__ import annotations

from calendar import monthrange
from datetime import date

WORK_TYPE_CATALOG: list[tuple[str, str]] = [
    ("litigation", "Судебные дела"),
    ("corporate", "Корпоративное право"),
    ("contracts", "Договорная работа"),
    ("consulting", "Консультации"),
    ("other", "Иное"),
]

PROJECT_STATUS_CATALOG: list[tuple[str, str]] = [
    ("active", "Активный"),
    ("closed", "Завершён"),
    ("archived", "В архиве"),
]

_LITIGATION_KEYS = ("court", "hearing", "суд", "иск", "litigation", "arbitration")
_CORPORATE_KEYS = ("corporate", "m&a", "корпорат", "due diligence", "dd ")
_CONTRACT_KEYS = ("contract", "draft", "договор", "document", "agreement")
_CONSULTING_KEYS = ("consult", "meeting", "консульт", "совещ", "call", "email")


def infer_work_type(task_name: str | None) -> tuple[str, str]:
    s = (task_name or "").strip().casefold()
    if not s:
        return WORK_TYPE_CATALOG[-1]
    if any(k in s for k in _LITIGATION_KEYS):
        return WORK_TYPE_CATALOG[0]
    if any(k in s for k in _CORPORATE_KEYS):
        return WORK_TYPE_CATALOG[1]
    if any(k in s for k in _CONTRACT_KEYS):
        return WORK_TYPE_CATALOG[2]
    if any(k in s for k in _CONSULTING_KEYS):
        return WORK_TYPE_CATALOG[3]
    return WORK_TYPE_CATALOG[-1]


def project_status_for_row(*, is_archived: bool, end_date: date | None, today: date) -> tuple[str, str]:
    if is_archived:
        return PROJECT_STATUS_CATALOG[2]
    if end_date is not None and end_date < today:
        return PROJECT_STATUS_CATALOG[1]
    return PROJECT_STATUS_CATALOG[0]


_MONTHS_RU = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


def month_period_bounds(work_date: date) -> tuple[date, date, str]:
    start = work_date.replace(day=1)
    last_day = monthrange(work_date.year, work_date.month)[1]
    end = date(work_date.year, work_date.month, last_day)
    label = f"{_MONTHS_RU[work_date.month]} {work_date.year}"
    return start, end, label
