from __future__ import annotations

from datetime import date


def is_project_closed_for_time_entries(
    *,
    is_archived: bool,
    end_date: date | None,
    as_of: date,
) -> bool:
    """True if new/edited time entries must not use this project on date `as_of`."""
    if is_archived:
        return True
    if end_date is not None and end_date < as_of:
        return True
    return False
