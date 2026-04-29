

from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

__all__ = [
    "local_today",
    "saturday_start_of_reporting_week",
    "work_week_start_end_inclusive",
    "previous_closed_saturday_fri_for_anchor",
    "now_in_submit_tz",
    "is_work_week_edit_deadline_passed",
]


def now_in_submit_tz() -> datetime:

    t = (os.environ.get("WEEKLY_SUBMIT_TZ", "UTC") or "UTC").strip() or "UTC"
    if t.upper() in ("UTC", "GMT", "Z"):
        return datetime.now(timezone.utc)
    return datetime.now(ZoneInfo(t))


def local_today(tz_name: str) -> date:

    tz = (tz_name or "UTC").strip() or "UTC"
    if tz.upper() in ("UTC", "GMT", "Z"):
        return datetime.now(timezone.utc).date()
    return datetime.now(ZoneInfo(tz)).date()


def saturday_start_of_reporting_week(d: date) -> date:

    return d - timedelta(days=(d.weekday() + 2) % 7)


def work_week_start_end_inclusive(d: date) -> tuple[date, date]:

    s = saturday_start_of_reporting_week(d)
    return s, s + timedelta(days=6)


def work_week_saturday_nine_closing_aware(week_start_saturday: date, *, tz_name: str) -> datetime:

    t = (tz_name or "UTC").strip() or "UTC"
    day = week_start_saturday + timedelta(days=7)
    clock = time(9, 0, 0)
    if t.upper() in ("UTC", "GMT", "Z"):
        return datetime.combine(day, clock, tzinfo=timezone.utc)
    return datetime.combine(day, clock, tzinfo=ZoneInfo(t))


def is_work_week_edit_deadline_passed(
    work_date: date,
    *,
    now: datetime | None = None,
    submit_tz: str | None = None,
) -> bool:

    w0, _w1 = work_week_start_end_inclusive(work_date)
    stz = (submit_tz or os.environ.get("WEEKLY_SUBMIT_TZ", "UTC") or "UTC").strip() or "UTC"
    n = now if now is not None else now_in_submit_tz()
    if n.tzinfo is None:
        raise ValueError("now must be timezone-aware when passed explicitly")
    close_at = work_week_saturday_nine_closing_aware(w0, tz_name=stz)
    return n >= close_at


def previous_closed_saturday_fri_for_anchor(anchor: date) -> tuple[date, date]:

    s = saturday_start_of_reporting_week(anchor)
    prev = s - timedelta(days=7)
    return prev, prev + timedelta(days=6)


def monday_of_same_iso_week(d: date) -> date:

    return d - timedelta(days=d.weekday())


def previous_closed_iso_week_range(anchor: date) -> tuple[date, date]:

    this_mon = monday_of_same_iso_week(anchor)
    prev_mon = this_mon - timedelta(days=7)
    prev_sun = prev_mon + timedelta(days=6)
    return prev_mon, prev_sun
