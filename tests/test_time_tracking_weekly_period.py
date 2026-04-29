

from datetime import date, datetime, timezone

from service_path import ensure_service_in_path

ensure_service_in_path("time_tracking")

from application.weekly_period import (
    is_work_week_edit_deadline_passed,
    monday_of_same_iso_week,
    previous_closed_iso_week_range,
    previous_closed_saturday_fri_for_anchor,
    saturday_start_of_reporting_week,
    work_week_saturday_nine_closing_aware,
    work_week_start_end_inclusive,
)


def test_saturday_start_wednesday_jan_2024() -> None:
    assert saturday_start_of_reporting_week(date(2024, 1, 10)) == date(2024, 1, 6)


def test_work_week_inclusive() -> None:
    s, e = work_week_start_end_inclusive(date(2024, 1, 9))
    assert s == date(2024, 1, 6)
    assert e == date(2024, 1, 12)


def test_previous_closed_when_anchor_is_saturday() -> None:
    d0, d1 = previous_closed_saturday_fri_for_anchor(date(2024, 1, 13))
    assert d0 == date(2024, 1, 6)
    assert d1 == date(2024, 1, 12)


def test_saturday_nine_utc_closing() -> None:

    c = work_week_saturday_nine_closing_aware(
        date(2024, 1, 6),
        tz_name="UTC",
    )
    assert c == datetime(2024, 1, 13, 9, 0, 0, tzinfo=timezone.utc)


def test_deadline_passed_around_saturday_nine_utc() -> None:
    wd = date(2024, 1, 10)
    before = datetime(2024, 1, 13, 8, 59, 0, tzinfo=timezone.utc)
    after = datetime(2024, 1, 13, 9, 0, 0, tzinfo=timezone.utc)
    assert not is_work_week_edit_deadline_passed(
        wd, now=before, submit_tz="UTC"
    )
    assert is_work_week_edit_deadline_passed(wd, now=after, submit_tz="UTC")


def test_monday_of_iso_week_wednesday_jan_2024() -> None:
    assert monday_of_same_iso_week(date(2024, 1, 10)) == date(2024, 1, 8)


def test_previous_closed_iso_week() -> None:
    d0, d1 = previous_closed_iso_week_range(date(2024, 1, 10))
    assert d0 == date(2024, 1, 1)
    assert d1 == date(2024, 1, 7)
