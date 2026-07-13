from __future__ import annotations

from datetime import date

from application.project_time_entry import (
    is_project_closed_for_time_entries,
    project_time_entry_block_detail,
)


def test_archived_flag_blocks() -> None:
    assert is_project_closed_for_time_entries(
        is_archived=True,
        end_date=None,
        as_of=date(2026, 6, 15),
    )


def test_paused_flag_blocks() -> None:
    assert is_project_closed_for_time_entries(
        is_archived=False,
        is_paused=True,
        end_date=None,
        as_of=date(2026, 6, 15),
    )


def test_paused_block_detail() -> None:
    assert "паузе" in project_time_entry_block_detail(is_archived=False, is_paused=True).lower()


def test_end_date_before_as_of_blocks() -> None:
    assert is_project_closed_for_time_entries(
        is_archived=False,
        end_date=date(2026, 6, 14),
        as_of=date(2026, 6, 15),
    )


def test_end_date_equal_as_of_allows() -> None:
    assert not is_project_closed_for_time_entries(
        is_archived=False,
        end_date=date(2026, 6, 15),
        as_of=date(2026, 6, 15),
    )


def test_active_project_allows() -> None:
    assert not is_project_closed_for_time_entries(
        is_archived=False,
        is_paused=False,
        end_date=None,
        as_of=date(2026, 6, 15),
    )
