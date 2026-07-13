"""Weekly edit lock: grace until Monday 12:00 even after submit."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

import application.weekly_submission_service as lock_mod
from application.weekly_period import (
    is_work_week_edit_deadline_passed,
    work_week_monday_twelve_closing_aware,
)
from application.weekly_submission_service import is_work_date_locked_for_user


def test_tashkent_monday_twelve_closing() -> None:
    # Week Sat 2026-07-04 .. Fri 2026-07-10 closes Mon 2026-07-13 12:00 Asia/Tashkent
    c = work_week_monday_twelve_closing_aware(date(2026, 7, 4), tz_name="Asia/Tashkent")
    assert c == datetime(2026, 7, 13, 12, 0, 0, tzinfo=ZoneInfo("Asia/Tashkent"))


def test_deadline_grace_until_monday_noon_tashkent() -> None:
    wd = date(2026, 7, 8)  # Wed inside Sat..Fri week
    before = datetime(2026, 7, 13, 11, 59, 0, tzinfo=ZoneInfo("Asia/Tashkent"))
    after = datetime(2026, 7, 13, 12, 0, 0, tzinfo=ZoneInfo("Asia/Tashkent"))
    assert not is_work_week_edit_deadline_passed(wd, now=before, submit_tz="Asia/Tashkent")
    assert is_work_week_edit_deadline_passed(wd, now=after, submit_tz="Asia/Tashkent")


@pytest.mark.asyncio
async def test_submitted_week_still_editable_before_monday_noon() -> None:
    unlock = AsyncMock()
    unlock.is_active_unlock = AsyncMock(return_value=False)

    with (
        patch.object(lock_mod, "TimeEntryEditUnlockRepository", return_value=unlock),
        patch.object(lock_mod, "is_work_week_edit_deadline_passed", return_value=False) as deadline,
        patch.dict("os.environ", {"WEEKLY_SUBMIT_TZ": "Asia/Tashkent"}),
    ):
        locked = await is_work_date_locked_for_user(MagicMock(), 42, date(2026, 7, 8))

    assert locked is False
    deadline.assert_called_once()


@pytest.mark.asyncio
async def test_locked_after_monday_noon_even_without_checking_submit() -> None:
    unlock = AsyncMock()
    unlock.is_active_unlock = AsyncMock(return_value=False)

    with (
        patch.object(lock_mod, "TimeEntryEditUnlockRepository", return_value=unlock),
        patch.object(lock_mod, "is_work_week_edit_deadline_passed", return_value=True),
        patch.dict("os.environ", {"WEEKLY_SUBMIT_TZ": "Asia/Tashkent"}),
    ):
        locked = await is_work_date_locked_for_user(MagicMock(), 42, date(2026, 7, 8))

    assert locked is True
