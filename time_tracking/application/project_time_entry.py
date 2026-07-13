from __future__ import annotations

from datetime import date


def is_project_closed_for_time_entries(
    *,
    is_archived: bool,
    end_date: date | None,
    as_of: date,
    is_paused: bool = False,
) -> bool:
    """True if new/edited time entries must not use this project on date `as_of`."""
    if is_archived:
        return True
    if is_paused:
        return True
    if end_date is not None and end_date < as_of:
        return True
    return False


def project_time_entry_block_detail(
    *,
    is_archived: bool,
    is_paused: bool = False,
    end_date: date | None = None,
    as_of: date | None = None,
) -> str:
    """User-facing reason when time entry against the project is refused."""
    if is_archived:
        return "Проект в архиве, списание времени недоступно"
    if is_paused:
        return "Проект на паузе, списание времени временно недоступно"
    if end_date is not None and as_of is not None and end_date < as_of:
        return "Срок проекта истёк, списание времени недоступно"
    return "Списание времени по проекту недоступно"
